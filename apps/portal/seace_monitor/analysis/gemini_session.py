"""Sesión Gemini: context cache + Q&A multi-turno sobre bases analizadas."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import AppConfig
from .gemini_client import (
    delete_remote_files,
    generate_content_with_retry,
    get_genai_client,
    today_anchor_peru,
    upload_file_with_retry,
)

logger = logging.getLogger(__name__)

PROMPT_FOLLOWUP_REL = Path("instrucciones/prompts/prompt_seace_followup.md")
SESSION_FILENAME = "gemini_session.json"
DEFAULT_CACHE_TTL = "86400s"


@dataclass
class GeminiTurn:
    role: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeminiTurn:
        return cls(role=str(data["role"]), text=str(data["text"]))


@dataclass
class GeminiSession:
    model: str
    cache_name: str | None = None
    cache_expire_at: str | None = None
    upload_paths: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    bootstrap_user: str = ""
    bootstrap_model: str = ""
    chat_turns: list[GeminiTurn] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeminiSession:
        turns = [GeminiTurn.from_dict(t) for t in data.get("chat_turns", [])]
        return cls(
            model=str(data.get("model", "")),
            cache_name=data.get("cache_name"),
            cache_expire_at=data.get("cache_expire_at"),
            upload_paths=list(data.get("upload_paths", [])),
            source_paths=list(data.get("source_paths", [])),
            bootstrap_user=str(data.get("bootstrap_user", "")),
            bootstrap_model=str(data.get("bootstrap_model", "")),
            chat_turns=turns,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "cache_name": self.cache_name,
            "cache_expire_at": self.cache_expire_at,
            "upload_paths": self.upload_paths,
            "source_paths": self.source_paths,
            "bootstrap_user": self.bootstrap_user,
            "bootstrap_model": self.bootstrap_model,
            "chat_turns": [t.to_dict() for t in self.chat_turns],
        }

    @property
    def chat_available(self) -> bool:
        return bool(self.cache_name and self.bootstrap_model)


def session_path_for_proc_dir(proc_dir: Path) -> Path:
    return proc_dir / "fast_analysis" / SESSION_FILENAME


def load_session(proc_dir: Path) -> GeminiSession | None:
    path = session_path_for_proc_dir(proc_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Sesión Gemini corrupta en %s", path)
        return None
    return GeminiSession.from_dict(data)


def save_session(proc_dir: Path, session: GeminiSession) -> Path:
    path = session_path_for_proc_dir(proc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _repo_root(config: AppConfig) -> Path:
    from ..tender_repo import resolve_tender_repo_root

    tender_repo = config.analysis.tender.repo_path or resolve_tender_repo_root()
    if tender_repo.exists():
        return tender_repo
    return Path(__file__).resolve().parents[4]


def _load_followup_prompt(config: AppConfig) -> str:
    path = _repo_root(config) / PROMPT_FOLLOWUP_REL
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "Responde preguntas de seguimiento sobre las bases adjuntas. "
        "Sé factual; indica si algo no está en el documento."
    )


def _followup_system_prompt(config: AppConfig) -> str:
    today, year, iso = today_anchor_peru()
    base = _load_followup_prompt(config).rstrip()
    return (
        f"{base}\n\n"
        f"## Ancla temporal\n\n"
        f"- Hoy es {today} ({iso}, America/Lima). Año en curso: {year}.\n"
    )


def _api_key(config: AppConfig) -> str:
    import os

    key = os.environ.get(config.analysis.fast_path.gemini_api_key_env, "")
    if not key:
        raise RuntimeError(
            f"Falta {config.analysis.fast_path.gemini_api_key_env} para chat Gemini"
        )
    return key


def _cache_ttl(config: AppConfig) -> str:
    return getattr(config.analysis.fast_path, "gemini_cache_ttl", None) or DEFAULT_CACHE_TTL


def _upload_paths_exist(upload_paths: list[str]) -> list[Path]:
    return [Path(raw) for raw in upload_paths if Path(raw).is_file()]


def delete_remote_cache(client, cache_name: str | None) -> None:
    if not cache_name:
        return
    try:
        client.caches.delete(name=cache_name)
        logger.info("Eliminado cache Gemini %s", cache_name)
    except Exception:
        logger.warning("No se pudo borrar cache Gemini %s", cache_name)


def cleanup_gemini_session(config: AppConfig, proc_dir: Path) -> None:
    session = load_session(proc_dir)
    if session is None:
        return
    try:
        client = get_genai_client(_api_key(config))
        delete_remote_cache(client, session.cache_name)
    except Exception:
        logger.warning("Limpieza cache Gemini falló para %s", proc_dir)
    path = session_path_for_proc_dir(proc_dir)
    if path.is_file():
        path.unlink()


def cache_is_valid(client, cache_name: str) -> bool:
    try:
        client.caches.get(name=cache_name)
        return True
    except Exception:
        return False


def create_document_cache_from_uploads(
    config: AppConfig,
    *,
    uploaded_files: list,
    upload_paths: list[Path],
    model: str,
) -> tuple[str, str | None]:
    """Crea cache desde archivos ya subidos a Gemini (evita doble upload)."""
    from google.genai import types

    api_key = _api_key(config)
    client = get_genai_client(api_key)
    file_parts = []
    for uploaded in uploaded_files:
        mime = uploaded.mime_type or "application/pdf"
        file_parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime))

    label = upload_paths[0].parent.parent.name[:40] if upload_paths else "seace"
    cached = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            contents=[types.Content(role="user", parts=file_parts)],
            system_instruction=_followup_system_prompt(config),
            display_name=f"seace-{label}",
            ttl=_cache_ttl(config),
        ),
    )
    expire_at = None
    exp = getattr(cached, "expire_time", None)
    if exp is not None:
        expire_at = exp.isoformat() if hasattr(exp, "isoformat") else str(exp)
    return cached.name, expire_at


def create_document_cache(
    config: AppConfig,
    *,
    upload_paths: list[Path],
    model: str,
) -> tuple[str, str | None]:
    """Sube PDFs, crea cache, borra uploads temporales."""
    api_key = _api_key(config)
    client = get_genai_client(api_key)
    uploaded_files = []
    try:
        for path in upload_paths:
            uploaded_files.append(upload_file_with_retry(api_key, path))
        return create_document_cache_from_uploads(
            config,
            uploaded_files=uploaded_files,
            upload_paths=upload_paths,
            model=model,
        )
    finally:
        delete_remote_files(client, uploaded_files)


def rebuild_cache_if_needed(
    config: AppConfig, proc_dir: Path, session: GeminiSession
) -> GeminiSession:
    api_key = _api_key(config)
    client = get_genai_client(api_key)
    if session.cache_name and cache_is_valid(client, session.cache_name):
        try:
            client.caches.update(
                name=session.cache_name,
                config={"ttl": _cache_ttl(config)},
            )
        except Exception:
            logger.warning("No se pudo renovar TTL del cache %s", session.cache_name)
        return session

    delete_remote_cache(client, session.cache_name)
    paths = _upload_paths_exist(session.upload_paths)
    if not paths:
        raise RuntimeError(
            "Los PDFs del análisis ya no están en disco; vuelve a analizar el proceso."
        )
    cache_name, expire_at = create_document_cache(
        config, upload_paths=paths, model=session.model
    )
    session.cache_name = cache_name
    session.cache_expire_at = expire_at
    save_session(proc_dir, session)
    return session


def initialize_session_after_analysis(
    config: AppConfig,
    proc_dir: Path,
    *,
    model: str,
    uploaded_files: list,
    upload_paths: list[Path],
    source_paths: list[Path],
    bootstrap_user: str,
    bootstrap_model: str,
) -> GeminiSession:
    cleanup_gemini_session(config, proc_dir)
    cache_name, expire_at = create_document_cache_from_uploads(
        config,
        uploaded_files=uploaded_files,
        upload_paths=upload_paths,
        model=model,
    )
    session = GeminiSession(
        model=model,
        cache_name=cache_name,
        cache_expire_at=expire_at,
        upload_paths=[str(p) for p in upload_paths],
        source_paths=[str(p) for p in source_paths],
        bootstrap_user=bootstrap_user,
        bootstrap_model=bootstrap_model,
        chat_turns=[],
    )
    save_session(proc_dir, session)
    return session


def _contents_for_question(session: GeminiSession, question: str) -> list:
    from google.genai import types

    contents: list[types.Content] = []
    if session.bootstrap_user:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=session.bootstrap_user)],
            )
        )
    if session.bootstrap_model:
        contents.append(
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=session.bootstrap_model)],
            )
        )
    for turn in session.chat_turns:
        contents.append(
            types.Content(
                role=turn.role,
                parts=[types.Part.from_text(text=turn.text)],
            )
        )
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=question.strip())])
    )
    return contents


def send_chat_message(
    config: AppConfig, proc_dir: Path, question: str
) -> tuple[str, GeminiSession]:
    question = question.strip()
    if not question:
        raise ValueError("La pregunta no puede estar vacía")
    if len(question) > 8000:
        raise ValueError("Pregunta demasiado larga")

    session = load_session(proc_dir)
    if session is None or not session.bootstrap_model:
        raise RuntimeError(
            "Este proceso no tiene sesión de chat. Analízalo de nuevo con el lector Gemini."
        )

    session = rebuild_cache_if_needed(config, proc_dir, session)
    api_key = _api_key(config)
    client = get_genai_client(api_key)

    from google.genai import types

    response = generate_content_with_retry(
        client,
        model=session.model,
        contents=_contents_for_question(session, question),
        config=types.GenerateContentConfig(cached_content=session.cache_name),
    )
    text = getattr(response, "text", None)
    if not text or not str(text).strip():
        raise RuntimeError("Gemini devolvió respuesta vacía")

    reply = str(text).strip()
    session.chat_turns.append(GeminiTurn(role="user", text=question))
    session.chat_turns.append(GeminiTurn(role="model", text=reply))
    save_session(proc_dir, session)
    return reply, session


def public_chat_payload(session: GeminiSession | None) -> dict[str, Any]:
    if session is None:
        return {"available": False, "turns": [], "message": "Sin sesión de chat."}
    if not session.chat_available:
        return {
            "available": False,
            "turns": [],
            "message": "Chat no disponible para este análisis.",
        }
    return {
        "available": True,
        "turns": [t.to_dict() for t in session.chat_turns],
        "message": None,
    }
