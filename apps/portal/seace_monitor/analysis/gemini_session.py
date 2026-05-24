"""Sesión Gemini: context cache + Q&A multi-turno sobre bases analizadas."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import AppConfig
from .analysis_lock import analysis_lock
from .gemini_client import (
    delete_remote_files,
    generate_content_with_retry,
    get_genai_client,
    today_anchor_peru,
    upload_file_with_retry,
)
from .gemini_orphans import log_gemini_orphan

logger = logging.getLogger(__name__)

PROMPT_FOLLOWUP_REL = Path("instrucciones/A_pre_portafolio/prompts/seace_followup.md")
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
    merge_fallback: bool = False
    merged_upload_path: str | None = None

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
            merge_fallback=bool(data.get("merge_fallback", False)),
            merged_upload_path=data.get("merged_upload_path"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "cache_name": self.cache_name,
            "cache_expire_at": self.cache_expire_at,
            "upload_paths": self.upload_paths,
            "source_paths": self.source_paths,
            "bootstrap_user": self.bootstrap_user,
            "bootstrap_model": self.bootstrap_model,
            "chat_turns": [t.to_dict() for t in self.chat_turns],
        }
        if self.merge_fallback:
            payload["merge_fallback"] = True
            payload["merged_upload_path"] = self.merged_upload_path
        return payload

    @property
    def chat_available(self) -> bool:
        return bool(self.cache_name and self.bootstrap_model)


def session_path_for_proc_dir(proc_dir: Path) -> Path:
    return proc_dir / "fast_analysis" / SESSION_FILENAME


def _path_to_store(proc_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(proc_dir.resolve()))
    except ValueError:
        return str(path)


def _resolve_stored_paths(proc_dir: Path, stored: list[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in stored:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        candidate = Path(raw)
        if candidate.is_absolute():
            if candidate.is_file():
                resolved.append(candidate)
                continue
            fallback = proc_dir / raw.lstrip("/")
            if fallback.is_file():
                resolved.append(fallback)
                continue
            by_name = proc_dir / "documentos" / candidate.name
            if by_name.is_file():
                resolved.append(by_name)
            continue
        rel = proc_dir / raw
        if rel.is_file():
            resolved.append(rel)
    return resolved


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
    payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=".gemini_session.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return path


def clean_run_scoped_artifacts(proc_dir: Path) -> None:
    """Limpia artefactos generados por runs previos (conserva sesión vigente)."""
    workspace = proc_dir / "fast_analysis"
    if not workspace.is_dir():
        return
    keep = {SESSION_FILENAME, "gemini_orphans.jsonl"}
    for item in workspace.iterdir():
        if item.name in keep:
            continue
        if item.is_dir():
            import shutil

            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


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


def delete_remote_cache(
    client, cache_name: str | None, *, proc_dir: Path | None = None
) -> bool:
    if not cache_name:
        return True
    try:
        client.caches.delete(name=cache_name)
        logger.info("Eliminado cache Gemini %s", cache_name)
        return True
    except Exception as exc:
        logger.warning("No se pudo borrar cache Gemini %s", cache_name)
        if proc_dir is not None:
            log_gemini_orphan(
                proc_dir, kind="cache", resource_id=cache_name, error=str(exc)
            )
        return False


def cleanup_gemini_session(config: AppConfig, proc_dir: Path) -> None:
    session = load_session(proc_dir)
    if session is None:
        return
    cache_deleted = True
    try:
        client = get_genai_client(_api_key(config))
        cache_deleted = delete_remote_cache(
            client, session.cache_name, proc_dir=proc_dir
        )
    except Exception as exc:
        logger.warning("Limpieza cache Gemini falló para %s", proc_dir)
        if session.cache_name:
            log_gemini_orphan(
                proc_dir,
                kind="cache",
                resource_id=session.cache_name,
                error=str(exc),
            )
        cache_deleted = False
    if cache_deleted:
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
    proc_dir: Path | None = None,
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
    try:
        cached = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                contents=[types.Content(role="user", parts=file_parts)],
                system_instruction=_followup_system_prompt(config),
                display_name=f"seace-{label}",
                ttl=_cache_ttl(config),
            ),
        )
    except Exception:
        delete_remote_files(client, uploaded_files, proc_dir=proc_dir)
        raise
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
    proc_dir: Path | None = None,
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
            proc_dir=proc_dir,
        )
    finally:
        delete_remote_files(client, uploaded_files, proc_dir=proc_dir)


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

    delete_remote_cache(client, session.cache_name, proc_dir=proc_dir)
    paths = _resolve_stored_paths(proc_dir, session.upload_paths)
    if not paths:
        raise RuntimeError(
            "Los PDFs del análisis ya no están en disco; vuelve a analizar el proceso."
        )
    cache_name, expire_at = create_document_cache(
        config, upload_paths=paths, model=session.model, proc_dir=proc_dir
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
    merge_fallback: bool = False,
    merged_upload_path: Path | None = None,
) -> GeminiSession:
    cleanup_gemini_session(config, proc_dir)
    cache_name, expire_at = create_document_cache_from_uploads(
        config,
        uploaded_files=uploaded_files,
        upload_paths=upload_paths,
        model=model,
        proc_dir=proc_dir,
    )
    session = GeminiSession(
        model=model,
        cache_name=cache_name,
        cache_expire_at=expire_at,
        upload_paths=[_path_to_store(proc_dir, p) for p in upload_paths],
        source_paths=[_path_to_store(proc_dir, p) for p in source_paths],
        bootstrap_user=bootstrap_user,
        bootstrap_model=bootstrap_model,
        chat_turns=[],
        merge_fallback=merge_fallback,
        merged_upload_path=(
            _path_to_store(proc_dir, merged_upload_path)
            if merged_upload_path
            else None
        ),
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

    with analysis_lock(proc_dir):
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
        "merge_fallback": session.merge_fallback,
    }
