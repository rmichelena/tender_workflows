"""Datos estructurados para vistas de detalle de procesos."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from ..parser import clean_cronograma_etapa, fechas_listado_from_cronograma_json
from ..db.models import Process
from ..document_storage import (
    MANIFEST_NAME,
    display_name_for_path,
    index_downloaded_by_uuid,
    manifest_path_for_doc,
    read_manifest,
    resolve_existing_download,
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
ANALYZED_FILES_NAME = "analyzed_files.json"


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
    is_new: bool = False


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
    fecha_publicacion: str = ""
    uuid: str = ""
    is_new: bool = False


@dataclass
class DocumentoNodo:
    rel_path: str | None
    nombre: str
    extension: str
    icon: str
    size_label: str
    etapa: str = ""
    tipo_documento: str = ""
    fecha_publicacion: str = ""
    origen: str = ""
    uuid: str = ""
    downloaded: bool = True
    previewable: bool = False
    selectable: bool = False
    default_checked: bool = False
    analyzed: bool = False
    is_new: bool = False
    is_folder: bool = False
    children: list[DocumentoNodo] = field(default_factory=list)


@dataclass
class CronogramaFila:
    etapa: str
    fecha_inicio: str
    fecha_fin: str
    fecha_inicio_prev: str | None = None
    fecha_fin_prev: str | None = None
    changed: bool = False
    is_new: bool = False
    is_removed: bool = False
    fecha_inicio_changed: bool = False
    fecha_fin_changed: bool = False


def _documentos_by_uuid(documentos_json: str | None) -> dict[str, dict]:
    if not documentos_json:
        return {}
    try:
        raw = json.loads(documentos_json)
    except json.JSONDecodeError:
        return {}
    index: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid", "")).strip()
        if uuid:
            index[uuid] = item
    return index


def _merge_doc_meta(meta: dict, uuid_index: dict[str, dict]) -> dict:
    uuid = str(meta.get("uuid", "")).strip()
    if uuid and uuid in uuid_index:
        merged = dict(uuid_index[uuid])
        merged.update(meta)
        return merged
    return meta


def _document_uuids_from_json(documentos_json: str | None) -> set[str]:
    if not documentos_json:
        return set()
    try:
        raw = json.loads(documentos_json)
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("uuid", "")).strip()
        for item in raw
        if isinstance(item, dict) and str(item.get("uuid", "")).strip()
    }


def _mark_new_documents(
    rows: list[DocumentoDescargado], prev_documentos_json: str | None
) -> None:
    prev_uuids = _document_uuids_from_json(prev_documentos_json)
    if not prev_uuids:
        return
    for row in rows:
        if row.uuid and row.uuid not in prev_uuids:
            row.is_new = True


def _mark_new_analyzable_files(
    rows: list[ArchivoAnalizable], prev_documentos_json: str | None
) -> None:
    prev_uuids = _document_uuids_from_json(prev_documentos_json)
    if not prev_uuids:
        return
    for row in rows:
        if row.uuid and row.uuid not in prev_uuids:
            row.is_new = True


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


def analyzed_files_path(proc_dir: Path) -> Path:
    return proc_dir / "fast_analysis" / ANALYZED_FILES_NAME


def _load_rel_paths_json(path: Path) -> set[str] | None:
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


def save_analysis_selection(proc_dir: Path, rel_paths: list[str]) -> None:
    path = selected_files_path(proc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rel_paths, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_analysis_selection(proc_dir: Path) -> set[str] | None:
    return _load_rel_paths_json(selected_files_path(proc_dir))


def save_analyzed_files(proc_dir: Path, rel_paths: list[str]) -> None:
    """Persiste qué documentos se enviaron al análisis exitoso más reciente."""
    path = analyzed_files_path(proc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [str(item).strip() for item in rel_paths if str(item).strip()]
    path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_analyzed_files(proc_dir: Path) -> set[str] | None:
    recorded = _load_rel_paths_json(analyzed_files_path(proc_dir))
    if recorded is not None:
        return recorded
    return load_analysis_selection(proc_dir)


def _assign_default_selection(rows: list[ArchivoAnalizable]) -> list[ArchivoAnalizable]:
    if not rows:
        return rows
    best_score = -1
    best_idx = 0
    for index, row in enumerate(rows):
        score = score_bases_candidate(Path(row.nombre))
        if score > best_score:
            best_score = score
            best_idx = index
    return [
        replace(row, default_checked=(index == best_idx))
        for index, row in enumerate(rows)
    ]


def _node_from_path(
    path: Path,
    docs_dir: Path,
    *,
    meta: dict | None = None,
    origen: str = "descarga SEACE",
) -> DocumentoNodo:
    rel = str(path.relative_to(docs_dir)).replace("\\", "/")
    merged = meta or {}
    nombre = str(merged.get("nombre") or path.name)
    suffix = path.suffix.lower()
    return DocumentoNodo(
        rel_path=rel,
        nombre=nombre,
        extension=suffix.lstrip(".") or "file",
        icon=file_icon_key(nombre),
        size_label=format_bytes(path.stat().st_size),
        etapa=str(merged.get("etapa", "") or ""),
        tipo_documento=str(merged.get("tipo_documento", "") or ""),
        fecha_publicacion=str(merged.get("fecha_publicacion", "") or ""),
        origen=origen,
        uuid=str(merged.get("uuid", "") or ""),
        downloaded=True,
        previewable=suffix == ".pdf",
        selectable=suffix in ANALYZABLE_SUFFIXES,
    )


def _build_extract_subtree(extract_dir: Path, docs_dir: Path) -> list[DocumentoNodo]:
    if not extract_dir.is_dir():
        return []
    nodes: list[DocumentoNodo] = []
    for item in sorted(
        extract_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
    ):
        if item.is_dir():
            children = _build_extract_subtree(item, docs_dir)
            if not children:
                continue
            nodes.append(
                DocumentoNodo(
                    rel_path=None,
                    nombre=item.name,
                    extension="carpeta",
                    icon="folder",
                    size_label="",
                    origen="",
                    is_folder=True,
                    children=children,
                )
            )
            continue
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        if suffix in ARCHIVE_SUFFIXES:
            continue
        nodes.append(
            _node_from_path(
                item,
                docs_dir,
                origen=f"extraído de {extract_dir.name}",
            )
        )
    return nodes


def _missing_document_node(meta: dict, uuid_index: dict[str, dict]) -> DocumentoNodo:
    merged = _merge_doc_meta(meta, uuid_index)
    uuid = str(merged.get("uuid", "") or "")
    nombre = str(merged.get("nombre") or uuid or "documento")
    ext = Path(nombre).suffix.lower().lstrip(".") or "file"
    tamano_kb = str(merged.get("tamano_kb", "") or "").strip()
    size_label = f"{tamano_kb} KB" if tamano_kb else "—"
    return DocumentoNodo(
        rel_path=None,
        nombre=nombre,
        extension=ext,
        icon=file_icon_key(nombre),
        size_label=size_label,
        etapa=str(merged.get("etapa", "") or ""),
        tipo_documento=str(merged.get("tipo_documento", "") or ""),
        fecha_publicacion=str(merged.get("fecha_publicacion", "") or ""),
        origen="descarga SEACE",
        uuid=uuid,
        downloaded=False,
    )


def _walk_document_nodes(nodes: list[DocumentoNodo]):
    for node in nodes:
        yield node
        yield from _walk_document_nodes(node.children)


def count_document_nodes(nodes: list[DocumentoNodo]) -> int:
    total = 0
    for node in _walk_document_nodes(nodes):
        if node.is_folder:
            continue
        total += 1
    return total


def flatten_selectable_leaves(nodes: list[DocumentoNodo]) -> list[ArchivoAnalizable]:
    rows: list[ArchivoAnalizable] = []
    for node in _walk_document_nodes(nodes):
        if not node.selectable or not node.rel_path:
            continue
        rows.append(
            ArchivoAnalizable(
                rel_path=node.rel_path,
                nombre=node.nombre,
                extension=node.extension,
                icon=node.icon,
                size_label=node.size_label,
                origen=node.origen,
                tipo_documento=node.tipo_documento,
                fecha_publicacion=node.fecha_publicacion,
                uuid=node.uuid,
                default_checked=node.default_checked,
                is_new=node.is_new,
            )
        )
    return rows


def _apply_default_selection_to_tree(nodes: list[DocumentoNodo]) -> None:
    leaves = [n for n in _walk_document_nodes(nodes) if n.selectable and n.rel_path]
    if not leaves:
        return
    best_score = -1
    best_idx = 0
    for index, leaf in enumerate(leaves):
        score = score_bases_candidate(Path(leaf.nombre))
        if score > best_score:
            best_score = score
            best_idx = index
    for index, leaf in enumerate(leaves):
        leaf.default_checked = index == best_idx


def _mark_new_document_nodes(
    nodes: list[DocumentoNodo], prev_documentos_json: str | None
) -> None:
    prev_uuids = _document_uuids_from_json(prev_documentos_json)
    if not prev_uuids:
        return
    for node in _walk_document_nodes(nodes):
        if node.uuid and node.uuid not in prev_uuids:
            node.is_new = True


def build_document_tree(
    process: Process,
    *,
    checked_paths: set[str] | None = None,
    analyzed_paths: set[str] | None = None,
    prev_documentos_json: str | None = None,
    apply_default_selection: bool = True,
) -> list[DocumentoNodo]:
    docs_dir = _documents_dir(process)
    if not docs_dir:
        return []

    by_path = _index_manifest_by_path(docs_dir)
    uuid_index = _documentos_by_uuid(process.documentos_json)
    manifest = read_manifest(docs_dir)
    doc_sources = manifest if manifest else json.loads(process.documentos_json or "[]")

    nodes: list[DocumentoNodo] = []
    seen_rels: set[str] = set()

    for doc in doc_sources:
        if not isinstance(doc, dict):
            continue
        path = resolve_existing_download(docs_dir, doc)
        merged = _merge_doc_meta(by_path.get(path.resolve(), doc) if path else doc, uuid_index)
        if path is None or not path.is_file():
            nodes.append(_missing_document_node(doc, uuid_index))
            continue

        rel = str(path.relative_to(docs_dir)).replace("\\", "/")
        if rel in seen_rels:
            continue
        seen_rels.add(rel)

        node = _node_from_path(path, docs_dir, meta=merged)
        if path.suffix.lower() in ARCHIVE_SUFFIXES:
            extract_dir = docs_dir / "_extracted" / path.stem
            node.children = _build_extract_subtree(extract_dir, docs_dir)
        nodes.append(node)

    if not manifest:
        for path in sorted(docs_dir.iterdir()):
            if not path.is_file() or path.name in _SKIP_NAMES:
                continue
            rel = path.name
            if rel in seen_rels:
                continue
            seen_rels.add(rel)
            meta = _merge_doc_meta(by_path.get(path.resolve(), {}), uuid_index)
            node = _node_from_path(path, docs_dir, meta=meta)
            if path.suffix.lower() in ARCHIVE_SUFFIXES:
                extract_dir = docs_dir / "_extracted" / path.stem
                node.children = _build_extract_subtree(extract_dir, docs_dir)
            nodes.append(node)

    if checked_paths is not None:
        for node in _walk_document_nodes(nodes):
            if node.selectable and node.rel_path:
                node.default_checked = node.rel_path in checked_paths
    elif apply_default_selection and not (
        process.analysis and process.analysis.status == "running"
    ):
        _apply_default_selection_to_tree(nodes)

    if analyzed_paths:
        for node in _walk_document_nodes(nodes):
            if node.rel_path and node.rel_path in analyzed_paths:
                node.analyzed = True

    if prev_documentos_json:
        _mark_new_document_nodes(nodes, prev_documentos_json)
        if process.watch_unread:
            for node in _walk_document_nodes(nodes):
                if node.is_new and node.selectable:
                    node.default_checked = True

    return nodes


def filter_new_document_nodes(nodes: list[DocumentoNodo]) -> list[DocumentoNodo]:
    """Sub-árbol con documentos marcados como nuevos (sin reconstruir el árbol)."""
    filtered: list[DocumentoNodo] = []
    for node in nodes:
        if node.is_folder:
            children = filter_new_document_nodes(node.children)
            if children:
                filtered.append(replace(node, children=children))
        elif node.is_new:
            filtered.append(node)
    return filtered


def list_analyzable_files(
    process: Process,
    *,
    checked_paths: set[str] | None = None,
    prev_documentos_json: str | None = None,
) -> list[ArchivoAnalizable]:
    tree = build_document_tree(
        process,
        checked_paths=checked_paths,
        prev_documentos_json=prev_documentos_json,
    )
    return flatten_selectable_leaves(tree)


def list_downloaded_documents(
    process: Process, *, prev_documentos_json: str | None = None
) -> list[DocumentoDescargado]:
    """Lista plana legacy; preferir build_document_tree en vistas de detalle."""
    flat: list[DocumentoDescargado] = []

    def walk(nodes: list[DocumentoNodo]) -> None:
        for node in nodes:
            if node.is_folder:
                walk(node.children)
                continue
            flat.append(
                DocumentoDescargado(
                    uuid=node.uuid,
                    nombre=node.nombre,
                    extension=node.extension,
                    icon=node.icon,
                    size_label=node.size_label,
                    etapa=node.etapa,
                    tipo_documento=node.tipo_documento,
                    fecha_publicacion=node.fecha_publicacion,
                    downloaded=node.downloaded,
                    filename=node.rel_path,
                    is_new=node.is_new,
                )
            )
            walk(node.children)

    walk(
        build_document_tree(
            process,
            prev_documentos_json=prev_documentos_json,
            apply_default_selection=False,
        )
    )
    return flat


def fechas_listado(process: Process) -> tuple[str, str]:
    """Fechas de fin (consultas y presentación) para columnas de listado."""
    fechas = fechas_listado_from_cronograma_json(
        process.cronograma_json,
        fallback_consultas=process.fecha_consultas or "",
        fallback_presentacion=process.fecha_presentacion or "",
    )
    return fechas.fecha_consultas, fechas.fecha_presentacion


def parse_cronograma(
    cronograma_json: str | None,
    *,
    prev_cronograma_json: str | None = None,
) -> list[CronogramaFila]:
    if not cronograma_json:
        return []
    try:
        raw = json.loads(cronograma_json)
    except json.JSONDecodeError:
        return []
    prev_by_etapa: dict[str, dict] = {}
    if prev_cronograma_json:
        try:
            for item in json.loads(prev_cronograma_json):
                if isinstance(item, dict):
                    key = clean_cronograma_etapa(str(item.get("etapa", "")))
                    prev_by_etapa[key] = item
        except json.JSONDecodeError:
            pass
    rows: list[CronogramaFila] = []
    current_etapas: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        etapa = clean_cronograma_etapa(str(item.get("etapa", "")))
        fecha_inicio = str(item.get("fecha_inicio", ""))
        fecha_fin = str(item.get("fecha_fin", ""))
        prev = prev_by_etapa.get(etapa)
        fecha_inicio_prev = str(prev.get("fecha_inicio", "")) if prev else None
        fecha_fin_prev = str(prev.get("fecha_fin", "")) if prev else None
        is_new = bool(prev_by_etapa) and prev is None
        inicio_changed = bool(prev and fecha_inicio_prev != fecha_inicio)
        fin_changed = bool(prev and fecha_fin_prev != fecha_fin)
        changed = is_new or inicio_changed or fin_changed
        current_etapas.add(etapa)
        rows.append(
            CronogramaFila(
                etapa=etapa,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                fecha_inicio_prev=fecha_inicio_prev if inicio_changed else None,
                fecha_fin_prev=fecha_fin_prev if fin_changed else None,
                changed=changed,
                is_new=is_new,
                fecha_inicio_changed=inicio_changed,
                fecha_fin_changed=fin_changed,
            )
        )
    for etapa, prev_item in prev_by_etapa.items():
        if etapa in current_etapas:
            continue
        rows.append(
            CronogramaFila(
                etapa=etapa,
                fecha_inicio="",
                fecha_fin="",
                fecha_inicio_prev=str(prev_item.get("fecha_inicio", "")),
                fecha_fin_prev=str(prev_item.get("fecha_fin", "")),
                changed=True,
                is_removed=True,
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


@dataclass
class WatchChangeLine:
    area: str
    label: str
    field: str
    kind: str
    old: str
    new: str
    area_label: str = ""
    kind_label: str = ""


@dataclass
class WatchChangelogEntry:
    at: str
    at_label: str
    changes: list[WatchChangeLine]


_CHANGE_KIND_LABELS = {
    "added": "Nuevo",
    "removed": "Eliminado",
    "modified": "Cambio",
}

_AREA_LABELS = {
    "cronograma": "Cronograma",
    "documento": "Documento",
    "proceso": "Proceso",
}


def parse_watch_changelog(raw: str | None) -> list[WatchChangelogEntry]:
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []

    entries: list[WatchChangelogEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        at = str(row.get("at", "") or "")
        changes_raw = row.get("changes")
        if not at or not isinstance(changes_raw, list):
            continue
        changes: list[WatchChangeLine] = []
        for item in changes_raw:
            if not isinstance(item, dict):
                continue
            changes.append(
                WatchChangeLine(
                    area=str(item.get("area", "") or ""),
                    label=str(item.get("label", "") or ""),
                    field=str(item.get("field", "") or ""),
                    kind=str(item.get("kind", "modified") or "modified"),
                    old=str(item.get("old", "") or ""),
                    new=str(item.get("new", "") or ""),
                    area_label=changelog_area_label(str(item.get("area", "") or "")),
                    kind_label=changelog_kind_label(
                        str(item.get("kind", "modified") or "modified")
                    ),
                )
            )
        if not changes:
            continue
        entries.append(
            WatchChangelogEntry(
                at=at,
                at_label=_format_changelog_timestamp(at),
                changes=changes,
            )
        )
    return entries


def _format_changelog_timestamp(at: str) -> str:
    try:
        dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        return local.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return at


def changelog_area_label(area: str) -> str:
    return _AREA_LABELS.get(area, area)


def changelog_kind_label(kind: str) -> str:
    return _CHANGE_KIND_LABELS.get(kind, kind)
