"""Análisis fast-path: documentos seleccionados → Gemini free reader (Markdown)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from ..config import AppConfig
from ..db.models import Process
from ..parser import clean_cronograma_etapa, fechas_listado_from_cronograma_json
from ..tender_repo import resolve_tender_repo_root
from .document_prep import (
    merge_pdfs,
    prepare_documents_for_llm,
    resolve_selected_documents,
    validate_gemini_upload_batch,
    validate_gemini_upload_size,
)
from .gemini_client import (
    delete_remote_files,
    generate_content_with_retry,
    get_genai_client,
    today_anchor_peru,
    upload_file_with_retry,
)
from .gemini_session import clean_run_scoped_artifacts, initialize_session_after_analysis
from .tender_bridge import parse_axis0_summary

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-pro"
PROMPT_REL = Path("instrucciones/A_pre_portafolio/prompts/seace_free_reader.md")
PROFILES_REL = Path("instrucciones/A_pre_portafolio/free_reader_profiles.yaml")


def _repo_root(config: AppConfig) -> Path:
    tender_repo = config.analysis.tender.repo_path or resolve_tender_repo_root()
    if tender_repo.exists():
        return tender_repo
    return Path(__file__).resolve().parents[4]


def _section_block(profile: dict[str, Any], sections: dict[str, Any]) -> str:
    include_sections = profile.get("include_sections", [])
    exclude_sections = set(profile.get("exclude_sections", []) or [])
    if include_sections == "all_except_excluded":
        keys = [key for key in sections if key not in exclude_sections]
    elif isinstance(include_sections, list):
        keys = [str(key) for key in include_sections]
    else:
        keys = [key for key, value in sections.items() if value.get("default", False)]
    lines = []
    for key in keys:
        section = sections.get(key)
        if not isinstance(section, dict):
            continue
        label = section.get("label")
        if label:
            lines.append(f"- {label}")
    return "\n".join(lines)


def _prompt_path_for_process(config: AppConfig, process: Process) -> Path:
    repo = _repo_root(config)
    profiles_path = repo / PROFILES_REL
    if not profiles_path.exists():
        return repo / PROMPT_REL
    raw = yaml.safe_load(profiles_path.read_text(encoding="utf-8")) or {}
    source = (process.source or "seace").strip().lower()
    profiles = raw.get("profiles", {}) or {}
    for profile in profiles.values():
        source_types = [str(item).lower() for item in profile.get("source_types", [])]
        if source in source_types:
            template = profile.get("prompt_template")
            if template:
                return profiles_path.parent / str(template)
    return repo / PROMPT_REL


def _load_system_prompt(config: AppConfig, process: Process) -> str:
    path = _prompt_path_for_process(config, process)
    if path.exists():
        base = path.read_text(encoding="utf-8")
    else:
        base = (
            "Lee las bases adjuntas y responde en Markdown narrativo. "
            "No extraigas cronograma del documento."
        )
    if "{{SECTIONS_BLOCK}}" in base:
        profiles_path = _repo_root(config) / PROFILES_REL
        raw = yaml.safe_load(profiles_path.read_text(encoding="utf-8")) or {}
        source = (process.source or "seace").strip().lower()
        profile = next(
            (
                item
                for item in (raw.get("profiles", {}) or {}).values()
                if source
                in [str(source_type).lower() for source_type in item.get("source_types", [])]
            ),
            {},
        )
        sections = raw.get("sections", {}) or {}
        base = base.replace("{{SECTIONS_BLOCK}}", _section_block(profile, sections))
    today, year, iso = today_anchor_peru()
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


def _build_user_context(process: Process, source_paths: list[Path]) -> str:
    today, year, iso = today_anchor_peru()
    source = process.source or "seace"
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
    if source == "seace":
        lines.extend(
            [
                f"Nomenclatura SEACE: {process.nomenclatura}",
                f"Entidad: {process.entity.nombre if process.entity else '—'}",
            ]
        )
    else:
        lines.append(f"Referencia {source}: {process.source_ref or process.nid_proceso}")
        if process.nomenclatura:
            lines.append(f"Nomenclatura / título: {process.nomenclatura}")
        if process.entity:
            lines.append(f"Entidad / comprador: {process.entity.nombre}")
    if process.objeto:
        lines.append(f"Objeto (ficha): {process.objeto}")
    if process.descripcion:
        label = "Descripción (ficha)" if source == "seace" else "Descripción"
        lines.append(f"{label}: {process.descripcion}")
    if source == "seace":
        fechas = fechas_listado_from_cronograma_json(
            process.cronograma_json,
            fallback_consultas=process.fecha_consultas or "",
            fallback_presentacion=process.fecha_presentacion or "",
        )
        if fechas.fecha_consultas:
            lines.append(f"Fin consultas (ficha SEACE): {fechas.fecha_consultas}")
        if fechas.fecha_presentacion:
            lines.append(
                f"Fin presentación propuestas (ficha SEACE): {fechas.fecha_presentacion}"
            )
        lines.append(
            "Recuerda: NO incluyas sección de cronograma del proceso; "
            "ese dato se toma de la ficha SEACE aparte."
        )
    return "\n".join(lines)


def run_gemini_free_reader(
    config: AppConfig,
    upload_paths: list[Path],
    source_paths: list[Path],
    process: Process,
) -> tuple[str, list, str]:
    """Genera resumen. Retorna (markdown, uploaded_files, bootstrap_user_text)."""
    fast_cfg = config.analysis.fast_path
    api_key = os.environ.get(fast_cfg.gemini_api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"Falta {fast_cfg.gemini_api_key_env} para análisis fast-path con Gemini"
        )

    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Instala google-genai: pip install google-genai") from exc

    client = get_genai_client(api_key)
    uploaded_files = []
    parts = []

    try:
        for path in upload_paths:
            uploaded = upload_file_with_retry(api_key, path)
            uploaded_files.append(uploaded)
            mime_type = uploaded.mime_type or "application/pdf"
            parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type))

        system = _load_system_prompt(config, process)
        user = _build_user_context(process, source_paths)
        parts.append(types.Part.from_text(text=user))

        response = generate_content_with_retry(
            client,
            model=fast_cfg.gemini_model or DEFAULT_MODEL,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(system_instruction=system),
        )
    except Exception:
        delete_remote_files(client, uploaded_files)
        raise

    text = getattr(response, "text", None)
    if not text:
        delete_remote_files(client, uploaded_files)
        raise RuntimeError("Gemini devolvió respuesta vacía")
    return text.strip(), uploaded_files, user


def append_seace_cronograma(markdown: str, process: Process) -> str:
    if (process.source or "seace") != "seace":
        return markdown
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


def _finalize_gemini_session(
    config: AppConfig,
    proc_dir: Path,
    *,
    uploaded_files: list,
    upload_paths: list[Path],
    sources: list[Path],
    bootstrap_user: str,
    bootstrap_model: str,
    merge_fallback: bool = False,
    merged_upload_path: Path | None = None,
) -> None:
    api_key = os.environ.get(config.analysis.fast_path.gemini_api_key_env, "")
    client = get_genai_client(api_key)
    try:
        initialize_session_after_analysis(
            config,
            proc_dir,
            model=config.analysis.fast_path.gemini_model or DEFAULT_MODEL,
            uploaded_files=uploaded_files,
            upload_paths=upload_paths,
            source_paths=sources,
            bootstrap_user=bootstrap_user,
            bootstrap_model=bootstrap_model,
            merge_fallback=merge_fallback,
            merged_upload_path=merged_upload_path,
        )
    finally:
        delete_remote_files(client, uploaded_files, proc_dir=proc_dir)


def run_fast_analysis(
    config: AppConfig,
    proc_dir: Path,
    documents_dir: Path,
    process: Process,
    selected_rel_paths: list[str],
) -> dict[str, Any]:
    workspace = proc_dir / "fast_analysis"
    clean_run_scoped_artifacts(proc_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    sources = resolve_selected_documents(documents_dir, selected_rel_paths)
    pdfs = prepare_documents_for_llm(sources, workspace)
    validate_gemini_upload_batch(pdfs)
    for source, pdf in zip(sources, pdfs, strict=True):
        if source.suffix.lower() == ".pdf":
            logger.info("PDF listo para Gemini: %s", source.name)
        else:
            logger.info(
                "Convertido %s → %s para Gemini",
                source.name,
                pdf.name,
            )
        validate_gemini_upload_size(pdf)

    uploaded_files: list = []
    bootstrap_user = ""
    merge_fallback = False
    merged_path: Path | None = None
    try:
        summary_core, uploaded_files, bootstrap_user = run_gemini_free_reader(
            config, pdfs, sources, process
        )
        upload_paths = pdfs
    except Exception as first_exc:
        if len(pdfs) <= 1:
            raise
        logger.warning("Gemini multi-archivo falló; combinando PDFs: %s", first_exc)
        merged = workspace / "merged_for_gemini.pdf"
        merge_pdfs(pdfs, merged)
        validate_gemini_upload_size(merged, merged=True)
        summary_core, uploaded_files, bootstrap_user = run_gemini_free_reader(
            config, [merged], sources, process
        )
        upload_paths = [merged]
        merge_fallback = True
        merged_path = merged
        bootstrap_user = (
            bootstrap_user.rstrip()
            + "\n\n"
            + f"Nota: los {len(sources)} PDF(s) originales se combinaron en un solo "
            "documento para el análisis y el chat."
        )

    _finalize_gemini_session(
        config,
        proc_dir,
        uploaded_files=uploaded_files,
        upload_paths=upload_paths,
        sources=sources,
        bootstrap_user=bootstrap_user,
        bootstrap_model=summary_core,
        merge_fallback=merge_fallback,
        merged_upload_path=merged_path,
    )

    summary_md = append_seace_cronograma(summary_core, process)

    summary_path = proc_dir / "free_reader_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")

    meta = {
        "mode": "fast_gemini",
        "selected_sources": [str(p) for p in sources],
        "upload_paths": [str(p) for p in upload_paths],
        "summary_path": str(summary_path),
        "gemini_model": config.analysis.fast_path.gemini_model,
        "chat_enabled": True,
        "merge_fallback": merge_fallback,
    }
    if merged_path is not None:
        meta["merged_upload_path"] = str(merged_path)
    (workspace / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    parsed = parse_axis0_summary(summary_path)
    return {
        **meta,
        **parsed,
        "free_reader_markdown": summary_md,
    }
