"""Análisis fast-path: documentos seleccionados → Gemini free reader (Markdown)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db.models import Process
from ..parser import clean_cronograma_etapa
from ..tender_repo import resolve_tender_repo_root
from .document_prep import merge_pdfs, prepare_documents_for_llm, resolve_selected_documents
from .tender_bridge import parse_axis0_summary

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-pro"
PROMPT_REL = Path("instrucciones/prompts/prompt_seace_free_reader.md")


def _repo_root(config: AppConfig) -> Path:
    tender_repo = config.analysis.tender.repo_path or resolve_tender_repo_root()
    if tender_repo.exists():
        return tender_repo
    return Path(__file__).resolve().parents[4]


def _load_system_prompt(config: AppConfig) -> str:
    path = _repo_root(config) / PROMPT_REL
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "Lee las bases adjuntas y responde en Markdown narrativo. "
        "No extraigas cronograma del documento."
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


def _build_user_context(process: Process, source_paths: list[Path]) -> str:
    lines = [
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
            uploaded = client.files.upload(file=str(path))
            uploaded = _wait_file_active(client, uploaded)
            uploaded_files.append(uploaded)
            mime_type = uploaded.mime_type or "application/pdf"
            parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type))

        system = _load_system_prompt(config)
        user = _build_user_context(process, source_paths)
        parts.append(types.Part.from_text(text=user))

        response = client.models.generate_content(
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
