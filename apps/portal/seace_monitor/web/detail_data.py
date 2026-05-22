"""Datos estructurados para vistas de detalle de procesos."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..parser import clean_cronograma_etapa, fechas_listado_from_cronograma_json
from ..db.models import Process
from ..document_storage import (
    MANIFEST_NAME,
    display_name_for_path,
    index_downloaded_by_uuid,
    manifest_path_for_doc,
    read_manifest,
)
from ..analysis.document_prep import ANALYZABLE_SUFFIXES, ARCHIVE_SUFFIXES, score_bases_candidate

_GENERIC_DOCS_RE = re.compile(r"^\d+\s+documento\(s\)\s+descargado\(s\)\s*$", re.I)
_SKIP_NAMES = {MANIFEST_NAME}

ICON_BY_EXT = {
    "pdf": "pdf",
    "doc": "docx",
    "docx": "docx",
    "xls": "xlsx",
    "xlsx": "xlsx",
    "dwg": "dwg",
    "zip": "zip",
    "rar": "rar",
    "7z": "zip",
}

DOWNLOAD_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".dwg": "application/acad",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
}

SELECTED_FILES_NAME = "selected_files.json"


@dataclass
class DocumentoDescargado:
    uuid: str
    nombre: str
    extension: str
    icon: str
    size_label: str
    etapa: str
    tipo_documento: str
    fecha_publicacion: str
    downloaded: bool
    filename: str | None


@dataclass
class ArchivoAnalizable:
    rel_path: str
    nombre: str
    extension: str
    icon: str
    size_label: str
    origen: str
    tipo_documento: str
    default_checked: bool


@dataclass
class CronogramaFila:
    etapa: str
    fecha_inicio: str
    fecha_fin: str


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def file_icon_key(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    return ICON_BY_EXT.get(ext, "file")


def incluye_display_text(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    if _GENERIC_DOCS_RE.match(text.strip()):
        return None
    return text.strip()


def _documents_dir(process: Process) -> Path | None:
    if not process.data_dir:
        return None
    docs_dir = Path(process.data_dir) / "documentos"
    return docs_dir if docs_dir.is_dir() else None


def _index_downloaded_files(docs_dir: Path) -> dict[str, Path]:
    return index_downloaded_by_uuid(docs_dir)


def _index_manifest_by_path(docs_dir: Path) -> dict[Path, dict]:
    index: dict[Path, dict] = {}
    for doc in read_manifest(docs_dir):
        path = manifest_path_for_doc(docs_dir, doc)
        if path is not None:
            index[path.resolve()] = doc
    return index


def media_type_for_path(path: Path) -> str:
    explicit = DOWNLOAD_MEDIA_TYPES.get(path.suffix.lower())
    if explicit:
        return explicit
    import mimetypes

    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def selected_files_path(proc_dir: Path) -> Path:
    return proc_dir / "fast_analysis" / SELECTED_FILES_NAME


def save_analysis_selection(proc_dir: Path, rel_paths: list[str]) -> None:
    path = selected_files_path(proc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rel_paths, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_analysis_selection(proc_dir: Path) -> set[str] | None:
    path = selected_files_path(proc_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list):
        return None
    cleaned = {str(item).strip() for item in raw if str(item).strip()}
    return cleaned or None


def _assign_default_selection(rows: list[ArchivoAnalizable]) -> None:
    if not rows:
        return
    best_score = -1
    best_idx = 0
    for index, row in enumerate(rows):
        score = score_bases_candidate(Path(row.nombre))
        if score > best_score:
            best_score = score
            best_idx = index
    for index, row in enumerate(rows):
        row.default_checked = index == best_idx


def list_analyzable_files(
    process: Process,
    *,
    checked_paths: set[str] | None = None,
) -> list[ArchivoAnalizable]:
    docs_dir = _documents_dir(process)
    if not docs_dir:
        return []

    by_path = _index_manifest_by_path(docs_dir)
    rows: list[ArchivoAnalizable] = []
    extract_root = docs_dir / "_extracted"

    for path in sorted(docs_dir.iterdir()):
        if not path.is_file() or path.name in _SKIP_NAMES:
            continue
        if path.suffix.lower() in ARCHIVE_SUFFIXES:
            continue
        if path.suffix.lower() not in ANALYZABLE_SUFFIXES:
            continue
        meta = by_path.get(path.resolve(), {})
        nombre = meta.get("nombre") or path.name
        tipo = meta.get("tipo_documento", "")
        rows.append(
            ArchivoAnalizable(
                rel_path=path.name,
                nombre=nombre,
                extension=path.suffix.lower().lstrip(".") or "file",
                icon=file_icon_key(nombre),
                size_label=format_bytes(path.stat().st_size),
                origen="descarga SEACE",
                tipo_documento=tipo,
                default_checked=False,
            )
        )

    if extract_root.exists():
        for path in sorted(extract_root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ANALYZABLE_SUFFIXES:
                continue
            rel = str(path.relative_to(docs_dir)).replace("\\", "/")
            parts = path.relative_to(extract_root).parts
            archive_label = parts[0] if parts else "archivo"
            rows.append(
                ArchivoAnalizable(
                    rel_path=rel,
                    nombre=path.name,
                    extension=path.suffix.lower().lstrip(".") or "file",
                    icon=file_icon_key(path.name),
                    size_label=format_bytes(path.stat().st_size),
                    origen=f"extraído de {archive_label}",
                    tipo_documento="",
                    default_checked=False,
                )
            )

    if checked_paths is not None:
        for row in rows:
            row.default_checked = row.rel_path in checked_paths
    else:
        _assign_default_selection(rows)
    return rows


def list_downloaded_documents(process: Process) -> list[DocumentoDescargado]:
    meta_list = json.loads(process.documentos_json or "[]")
    docs_dir = _documents_dir(process)
    on_disk = _index_downloaded_files(docs_dir) if docs_dir else {}

    rows: list[DocumentoDescargado] = []
    seen_paths: set[str] = set()

    for meta in meta_list:
        uuid = meta.get("uuid", "")
        nombre = meta.get("nombre") or uuid or "documento"
        path = on_disk.get(uuid) if uuid else None
        if path is None and docs_dir:
            path = manifest_path_for_doc(docs_dir, meta)
        ext = Path(nombre).suffix.lower().lstrip(".") or (
            path.suffix.lower().lstrip(".") if path else ""
        )
        if path:
            size_label = format_bytes(path.stat().st_size)
            filename = path.name
            downloaded = True
        else:
            tamano_kb = str(meta.get("tamano_kb", "") or "").strip()
            size_label = f"{tamano_kb} KB" if tamano_kb else "—"
            filename = None
            downloaded = False

        rows.append(
            DocumentoDescargado(
                uuid=uuid,
                nombre=nombre,
                extension=ext or "file",
                icon=file_icon_key(nombre),
                size_label=size_label,
                etapa=meta.get("etapa", ""),
                tipo_documento=meta.get("tipo_documento", ""),
                fecha_publicacion=meta.get("fecha_publicacion", ""),
                downloaded=downloaded,
                filename=filename,
            )
        )
        if uuid:
            seen_paths.add(uuid)
        if path is not None:
            seen_paths.add(str(path.resolve()))

    if docs_dir:
        extract_root = docs_dir / "_extracted"
        if extract_root.exists():
            for path in sorted(extract_root.rglob("*")):
                if not path.is_file():
                    continue
                rel_key = str(path.relative_to(docs_dir)).replace("\\", "/")
                if rel_key in seen_paths:
                    continue
                seen_paths.add(rel_key)
                rows.append(
                    DocumentoDescargado(
                        uuid="",
                        nombre=path.name,
                        extension=path.suffix.lower().lstrip(".") or "file",
                        icon=file_icon_key(path.name),
                        size_label=format_bytes(path.stat().st_size),
                        etapa="",
                        tipo_documento=f"extraído ({path.relative_to(extract_root).parts[0]})",
                        fecha_publicacion="",
                        downloaded=True,
                        filename=rel_key.replace("\\", "/"),
                    )
                )

        for path in sorted(docs_dir.iterdir()):
            if not path.is_file() or path.name in _SKIP_NAMES:
                continue
            key = str(path.resolve())
            if key in seen_paths:
                continue
            meta = _index_manifest_by_path(docs_dir).get(path.resolve(), {})
            seen_paths.add(key)
            rows.append(
                DocumentoDescargado(
                    uuid=meta.get("uuid", ""),
                    nombre=meta.get("nombre") or path.name,
                    extension=path.suffix.lower().lstrip(".") or "file",
                    icon=file_icon_key(path.name),
                    size_label=format_bytes(path.stat().st_size),
                    etapa="",
                    tipo_documento="",
                    fecha_publicacion="",
                    downloaded=True,
                    filename=path.name,
                )
            )

    return rows


def fechas_listado(process: Process) -> tuple[str, str]:
    """Fechas de fin (consultas y presentación) para columnas de listado."""
    fechas = fechas_listado_from_cronograma_json(
        process.cronograma_json,
        fallback_consultas=process.fecha_consultas or "",
        fallback_presentacion=process.fecha_presentacion or "",
    )
    return fechas.fecha_consultas, fechas.fecha_presentacion


def parse_cronograma(cronograma_json: str | None) -> list[CronogramaFila]:
    if not cronograma_json:
        return []
    try:
        raw = json.loads(cronograma_json)
    except json.JSONDecodeError:
        return []
    rows: list[CronogramaFila] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append(
            CronogramaFila(
                etapa=clean_cronograma_etapa(str(item.get("etapa", ""))),
                fecha_inicio=str(item.get("fecha_inicio", "")),
                fecha_fin=str(item.get("fecha_fin", "")),
            )
        )
    return rows


def resolve_document_path(process: Process, filename: str) -> Path | None:
    docs_dir = _documents_dir(process)
    if not docs_dir:
        return None
    rel = filename.strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    candidate = (docs_dir / rel).resolve()
    try:
        candidate.relative_to(docs_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def download_filename_for_path(process: Process, path: Path) -> str:
    docs_dir = _documents_dir(process)
    if docs_dir is None:
        return path.name
    return display_name_for_path(docs_dir, path)
