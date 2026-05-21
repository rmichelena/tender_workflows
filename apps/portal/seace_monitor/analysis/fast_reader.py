"""Análisis fast-path: documentos seleccionados → Gemini free reader (Markdown)."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..db.models import Process
from ..parser import clean_cronograma_etapa, fechas_listado_from_cronograma_json
from ..tender_repo import resolve_tender_repo_root
from .document_prep import merge_pdfs, prepare_documents_for_llm, resolve_selected_documents
from .tender_bridge import parse_axis0_summary

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-pro"
PROMPT_REL = Path("instrucciones/prompts/prompt_seace_free_reader.md")
_GEMINI_RETRY_ATTEMPTS = 5
_GEMINI_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_LIMA_TZ = ZoneInfo("America/Lima")
_MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _today_anchor_peru() -> tuple[str, int, str]:
    now = datetime.now(_LIMA_TZ)
    mes = _MESES_ES[now.month - 1]
    human = f"{now.day} de {mes} de {now.year}"
    return human, now.year, now.strftime("%Y-%m-%d")


def _repo_root(config: AppConfig) -> Path:
    tender_repo = config.analysis.tender.repo_path or resolve_tender_repo_root()
    if tender_repo.exists():
        return tender_repo
    return Path(__file__).resolve().parents[4]


def _load_system_prompt(config: AppConfig) -> str:
    path = _repo_root(config) / PROMPT_REL
    if path.exists():
        base = path.read_text(encoding="utf-8")
    else:
        base = (
            "Lee las bases adjuntas y responde en Markdown narrativo. "
            "No extraigas cronograma del documento."
        )
    today, year, iso = _today_anchor_peru()
    return (
        f"{base.rstrip()}\n\n"
        f"## Ancla temporal (obligatorio)\n\n"
        f"- **Hoy es {today}** (`{iso}`, America/Lima). Año en curso: **{year}**.\n"
        f"- Evalúa fechas y normativa citada desde esa fecha; no asumas que estamos en 2024 "
        f"ni en un año anterior al de la convocatoria.\n"
        f"- Leyes/decretos con vigencia en {year - 1} o {year} (p. ej. Ley N° 32069, "
        f"D.S. N° 009-2025-EF) son marco legal **vigente o aplicable**, no «futuro» ni "
        f"«próxima reforma», salvo texto expreso en contrario en las bases.\n"
        f"- En «Dudas / puntos a verificar» **no** incluyas avisos meta sobre plantillas "
        f"SEACE, años «prospectivos», ni «nuevo marco legal entrante» cuando ya estamos en "
        f"{year}. Reserva esa sección para ambigüedades contractuales, OCR dudoso o "
        f"contradicciones reales del documento.\n"
    )


def _wait_file_active(client, uploaded) -> Any:
    file = uploaded
    state = getattr(file, "state", None)
    state_name = getattr(state, "name", None) if state else None
    if state_name != "PROCESSING":
        return file

    deadline = time.time() + 300
    while state_name == "PROCESSING":
        if time.time() > deadline:
            raise RuntimeError("Timeout esperando procesamiento del archivo en Gemini")
        time.sleep(2)
        file = client.files.get(name=file.name)
        state = getattr(file, "state", None)
        state_name = getattr(state, "name", None) if state else None
    return file


def _is_retryable_gemini_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in _GEMINI_RETRYABLE_STATUS:
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "service unavailable",
            "upload has already been terminated",
            "resource exhausted",
            "deadline exceeded",
            "internal error",
        )
    )


def _gemini_retry_delay(attempt: int) -> float:
    return min(2**attempt, 30)


def _upload_file_with_retry(api_key: str, path: Path):
    from google import genai

    last_exc: Exception | None = None
    for attempt in range(1, _GEMINI_RETRY_ATTEMPTS + 1):
        client = genai.Client(api_key=api_key)
        try:
            uploaded = client.files.upload(file=str(path))
            return _wait_file_active(client, uploaded)
        except Exception as exc:
            last_exc = exc
            if attempt >= _GEMINI_RETRY_ATTEMPTS or not _is_retryable_gemini_error(exc):
                raise
            delay = _gemini_retry_delay(attempt)
            logger.warning(
                "Reintento %s/%s subida a Gemini (%s): %s; espera %ss",
                attempt,
                _GEMINI_RETRY_ATTEMPTS,
                path.name,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"No se pudo subir {path.name} a Gemini")


def _generate_content_with_retry(client, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, _GEMINI_RETRY_ATTEMPTS + 1):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= _GEMINI_RETRY_ATTEMPTS or not _is_retryable_gemini_error(exc):
                raise
            delay = _gemini_retry_delay(attempt)
            logger.warning(
                "Reintento %s/%s generateContent Gemini: %s; espera %ss",
                attempt,
                _GEMINI_RETRY_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini generateContent falló tras reintentos")


def _build_user_context(process: Process, source_paths: list[Path]) -> str:
    today, year, iso = _today_anchor_peru()
    lines = [
        "## Referencia temporal",
        f"- Fecha de hoy: {today} ({iso}, America/Lima)",
        f"- Año en curso: {year}",
        "- Usa esta referencia al interpretar fechas y normativa del documento.",
        "",
        "Analiza los documentos adjuntos (bases y anexos seleccionados por el usuario).",
        f"Archivos ({len(source_paths)}):",
    ]
    for path in source_paths:
        lines.append(f"- {path.name}")
    lines.extend(
        [
            f"Nomenclatura SEACE: {process.nomenclatura}",
            f"Entidad: {process.entity.nombre if process.entity else '—'}",
        ]
    )
    if process.objeto:
        lines.append(f"Objeto (ficha): {process.objeto}")
    if process.descripcion:
        lines.append(f"Descripción (ficha): {process.descripcion}")
    fechas = fechas_listado_from_cronograma_json(
        process.cronograma_json,
        fallback_consultas=process.fecha_consultas or "",
        fallback_presentacion=process.fecha_presentacion or "",
    )
    if fechas.fecha_consultas:
        lines.append(f"Fin consultas (ficha SEACE): {fechas.fecha_consultas}")
    if fechas.fecha_presentacion:
        lines.append(f"Fin presentación propuestas (ficha SEACE): {fechas.fecha_presentacion}")
    lines.append(
        "Recuerda: NO incluyas sección de cronograma del proceso; "
        "ese dato se toma de la ficha SEACE aparte."
    )
    return "\n".join(lines)


def append_seace_cronograma(markdown: str, process: Process) -> str:
    if not process.cronograma_json:
        return markdown
    try:
        rows = json.loads(process.cronograma_json)
    except json.JSONDecodeError:
        return markdown
    if not rows:
        return markdown

    lines = [
        markdown.rstrip(),
        "",
        "## Cronograma (ficha SEACE — no proviene de las bases)",
        "",
        "| Etapa | Inicio | Fin |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        etapa = clean_cronograma_etapa(str(row.get("etapa", "")))
        inicio = str(row.get("fecha_inicio", "") or "—")
        fin = str(row.get("fecha_fin", "") or "—")
        lines.append(f"| {etapa} | {inicio} | {fin} |")
    return "\n".join(lines) + "\n"


def run_gemini_free_reader(
    config: AppConfig,
    upload_paths: list[Path],
    source_paths: list[Path],
    process: Process,
) -> str:
    fast_cfg = config.analysis.fast_path
    api_key = os.environ.get(fast_cfg.gemini_api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"Falta {fast_cfg.gemini_api_key_env} para análisis fast-path con Gemini"
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Instala google-genai: pip install google-genai") from exc

    client = genai.Client(api_key=api_key)
    uploaded_files = []
    parts = []

    try:
        for path in upload_paths:
            uploaded = _upload_file_with_retry(api_key, path)
            uploaded_files.append(uploaded)
            mime_type = uploaded.mime_type or "application/pdf"
            parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type))

        system = _load_system_prompt(config)
        user = _build_user_context(process, source_paths)
        parts.append(types.Part.from_text(text=user))

        response = _generate_content_with_retry(
            client,
            model=fast_cfg.gemini_model or DEFAULT_MODEL,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(system_instruction=system),
        )
    except Exception:
        for uploaded in uploaded_files:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                logger.warning(
                    "No se pudo borrar archivo temporal en Gemini: %s", uploaded.name
                )
        raise

    for uploaded in uploaded_files:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            logger.warning(
                "No se pudo borrar archivo temporal en Gemini: %s", uploaded.name
            )

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini devolvió respuesta vacía")
    return text.strip()


def run_fast_analysis(
    config: AppConfig,
    proc_dir: Path,
    documents_dir: Path,
    process: Process,
    selected_rel_paths: list[str],
) -> dict[str, Any]:
    workspace = proc_dir / "fast_analysis"
    workspace.mkdir(parents=True, exist_ok=True)

    sources = resolve_selected_documents(documents_dir, selected_rel_paths)
    pdfs = prepare_documents_for_llm(sources, workspace)
    for source, pdf in zip(sources, pdfs, strict=True):
        if source.suffix.lower() == ".pdf":
            logger.info("PDF listo para Gemini: %s", source.name)
        else:
            logger.info(
                "Convertido %s → %s para Gemini",
                source.name,
                pdf.name,
            )

    try:
        summary_core = run_gemini_free_reader(config, pdfs, sources, process)
        upload_paths = pdfs
    except Exception as first_exc:
        if len(pdfs) <= 1:
            raise
        logger.warning("Gemini multi-archivo falló; combinando PDFs: %s", first_exc)
        merged = workspace / "merged_for_gemini.pdf"
        merge_pdfs(pdfs, merged)
        summary_core = run_gemini_free_reader(config, [merged], sources, process)
        upload_paths = [merged]

    summary_md = append_seace_cronograma(summary_core, process)

    summary_path = proc_dir / "free_reader_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")

    meta = {
        "mode": "fast_gemini",
        "selected_sources": [str(p) for p in sources],
        "upload_paths": [str(p) for p in upload_paths],
        "summary_path": str(summary_path),
        "gemini_model": config.analysis.fast_path.gemini_model,
    }
    (workspace / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    parsed = parse_axis0_summary(summary_path)
    return {
        **meta,
        **parsed,
        "free_reader_markdown": summary_md,
    }
