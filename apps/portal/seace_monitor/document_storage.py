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
        # Legacy: archivo guardado como {uuid}.pdf
        if path.stem in by_uuid:
            continue
        if len(path.stem) == 36 and path.stem.count("-") == 4:
            by_uuid[path.stem] = path
    return by_uuid


def resolve_existing_download(docs_dir: Path, doc: dict) -> Path | None:
    """Localiza un archivo ya descargado para este doc (uuid / manifest / nombre)."""
    uuid = str(doc.get("uuid", "")).strip()
    if not uuid:
        return None

    canonical = docs_dir / sanitize_download_filename(doc.get("nombre") or uuid, uuid)
    if canonical.is_file() and canonical.stat().st_size > 0:
        return canonical

    by_uuid = index_downloaded_by_uuid(docs_dir)
    path = by_uuid.get(uuid)
    if path is not None and path.is_file() and path.stat().st_size > 0:
        return path

    manifest_path = manifest_path_for_doc(docs_dir, doc)
    if manifest_path is not None and manifest_path.stat().st_size > 0:
        return manifest_path

    nombre = doc.get("nombre") or uuid
    ext = Path(nombre).suffix or ".pdf"
    legacy = docs_dir / f"{uuid}{ext}"
    if legacy.is_file() and legacy.stat().st_size > 0:
        return legacy
    return None


def prefer_canonical_archivo(docs_dir: Path, doc: dict) -> None:
    """Apunta manifest al nombre canónico y elimina copias numeradas (_2, _3)."""
    uuid = str(doc.get("uuid", "")).strip()
    if not uuid:
        return
    canonical_name = sanitize_download_filename(doc.get("nombre") or uuid, uuid)
    canonical = docs_dir / canonical_name
    if not canonical.is_file() or canonical.stat().st_size == 0:
        return

    stem, ext = Path(canonical_name).stem, Path(canonical_name).suffix
    for path in docs_dir.glob(f"{stem}_*{ext}"):
        if path.name == canonical_name or not path.is_file():
            continue
        path.unlink()

    doc["archivo"] = canonical_name


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
        if current.name == target_name:
            doc["archivo"] = current.name
            continue

        target = allocate_unique_path(docs_dir, target_name)
        if current.resolve() != target.resolve():
            current.rename(target)

        doc["archivo"] = target.name


def prepare_download_dest(docs_dir: Path, doc: dict) -> tuple[Path, bool]:
    """Ruta destino y si ya existe (skip download)."""
    uuid = doc["uuid"]
    nombre = doc.get("nombre") or uuid
    existing = resolve_existing_download(docs_dir, doc)
    if existing is not None:
        doc["archivo"] = existing.name
        prefer_canonical_archivo(docs_dir, doc)
        return existing, True

    dest = allocate_unique_path(
        docs_dir, sanitize_download_filename(nombre, uuid)
    )
    doc["archivo"] = dest.name
    return dest, False


def cleanup_partial_downloads(docs_dir: Path) -> None:
    """Elimina archivos parciales o vacíos tras un fallo de descarga."""
    if not docs_dir.is_dir():
        return
    for path in docs_dir.glob("*.part"):
        path.unlink(missing_ok=True)
    for path in docs_dir.iterdir():
        if path.is_file() and path.name != MANIFEST_NAME and path.stat().st_size == 0:
            path.unlink(missing_ok=True)
