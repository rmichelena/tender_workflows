"""Nombres legibles para documentos descargados de SEACE."""

from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST_NAME = "manifest.json"
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 180


def sanitize_download_filename(nombre: str, uuid: str = "") -> str:
    """Nombre seguro en disco a partir del nombre SEACE."""
    base = Path(nombre.replace("\\", "/")).name.strip()
    safe = _INVALID_CHARS.sub("_", base)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if not safe or safe in {".", ".."}:
        safe = uuid or "documento"
    stem = Path(safe).stem
    ext = Path(safe).suffix
    if not ext:
        ext = ".pdf"
        stem = safe
    if len(stem) + len(ext) > _MAX_FILENAME_LEN:
        stem = stem[: _MAX_FILENAME_LEN - len(ext)]
    return f"{stem}{ext}"


def allocate_unique_path(docs_dir: Path, filename: str) -> Path:
    dest = docs_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    ext = Path(filename).suffix
    index = 2
    while True:
        candidate = docs_dir / f"{stem}_{index}{ext}"
        if not candidate.exists():
            return candidate
        index += 1


def read_manifest(docs_dir: Path) -> list[dict]:
    manifest_path = docs_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_manifest(docs_dir: Path, docs: list[dict]) -> None:
    (docs_dir / MANIFEST_NAME).write_text(
        json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def manifest_path_for_doc(docs_dir: Path, doc: dict) -> Path | None:
    archivo = doc.get("archivo")
    if archivo:
        path = docs_dir / Path(archivo).name
        if path.is_file():
            return path

    uuid = doc.get("uuid", "")
    nombre = doc.get("nombre") or uuid
    ext = Path(nombre).suffix or ".pdf"
    legacy = docs_dir / f"{uuid}{ext}"
    if legacy.is_file():
        return legacy
    return None


def index_downloaded_by_uuid(docs_dir: Path) -> dict[str, Path]:
    by_uuid: dict[str, Path] = {}
    for doc in read_manifest(docs_dir):
        uuid = doc.get("uuid", "")
        if not uuid:
            continue
        path = manifest_path_for_doc(docs_dir, doc)
        if path is not None:
            by_uuid[uuid] = path

    for path in docs_dir.iterdir():
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        if path.stem not in by_uuid:
            by_uuid[path.stem] = path
    return by_uuid


def display_name_for_path(docs_dir: Path, path: Path) -> str:
    rel_name = path.name
    for doc in read_manifest(docs_dir):
        archivo = doc.get("archivo")
        if archivo and Path(archivo).name == rel_name:
            return doc.get("nombre") or rel_name
        uuid = doc.get("uuid", "")
        nombre = doc.get("nombre") or uuid
        ext = Path(nombre).suffix or ".pdf"
        if uuid and path.name == f"{uuid}{ext}":
            return nombre
    return rel_name


def normalize_legacy_filenames(docs_dir: Path, docs: list[dict]) -> None:
    """Renombra archivos UUID legacy al nombre legible del manifest."""
    for doc in docs:
        uuid = doc.get("uuid", "")
        if not uuid:
            continue
        current = manifest_path_for_doc(docs_dir, doc)
        if current is None:
            continue

        nombre = doc.get("nombre") or uuid
        target_name = sanitize_download_filename(nombre, uuid)
        target = allocate_unique_path(docs_dir, target_name)

        if current.resolve() != target.resolve():
            if target.exists():
                target = allocate_unique_path(docs_dir, target_name)
            current.rename(target)

        doc["archivo"] = target.name


def prepare_download_dest(docs_dir: Path, doc: dict) -> tuple[Path, bool]:
    """Ruta destino y si ya existe (skip download)."""
    uuid = doc["uuid"]
    nombre = doc.get("nombre") or uuid
    existing = manifest_path_for_doc(docs_dir, doc)
    if existing is not None:
        doc["archivo"] = existing.name
        return existing, True

    dest = allocate_unique_path(
        docs_dir, sanitize_download_filename(nombre, uuid)
    )
    doc["archivo"] = dest.name
    return dest, False
