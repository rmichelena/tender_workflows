"""Nombres legibles para documentos descargados de SEACE."""

from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST_NAME = "manifest.json"
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SIZE_LABEL_RE = re.compile(
    r"^\(?\s*\d+(?:\.\d+)?\s*(?:KB|MB|GB|B)\s*\)?$",
    re.IGNORECASE,
)
_MAX_FILENAME_LEN = 180


def looks_like_size_label(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    stem = Path(text.replace("\\", "/")).name
    if stem.lower().endswith(".pdf"):
        stem = Path(stem).stem
    return bool(_SIZE_LABEL_RE.match(stem))


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

    by_uuid = index_downloaded_by_uuid(docs_dir)
    path = by_uuid.get(uuid)
    if path is not None and path.is_file() and path.stat().st_size > 0:
        return path

    nombre = str(doc.get("nombre") or "").strip()
    if nombre and not looks_like_size_label(nombre):
        canonical = docs_dir / sanitize_download_filename(nombre, uuid)
        if canonical.is_file() and canonical.stat().st_size > 0:
            return canonical

    manifest_path = manifest_path_for_doc(docs_dir, doc)
    if manifest_path is not None and manifest_path.stat().st_size > 0:
        return manifest_path

    ext = Path(nombre).suffix if nombre else ".pdf"
    if not ext:
        ext = ".pdf"
    legacy = docs_dir / f"{uuid}{ext}"
    if legacy.is_file() and legacy.stat().st_size > 0:
        return legacy
    return None


def sync_doc_from_existing(docs_dir: Path, doc: dict) -> None:
    """Alinea manifest con un archivo ya descargado (uuid / archivo previo)."""
    existing = resolve_existing_download(docs_dir, doc)
    if existing is None:
        return
    doc["archivo"] = existing.name
    stored_nombre = ""
    for item in read_manifest(docs_dir):
        if item.get("uuid") == doc.get("uuid"):
            candidate = str(item.get("nombre") or "").strip()
            if candidate and not looks_like_size_label(candidate):
                stored_nombre = candidate
            break
    parsed_nombre = str(doc.get("nombre") or "").strip()
    if stored_nombre:
        doc["nombre"] = stored_nombre
    elif looks_like_size_label(parsed_nombre) or not parsed_nombre:
        doc["nombre"] = existing.name


def provisional_download_filename(doc: dict) -> str:
    uuid = str(doc.get("uuid", "")).strip() or "documento"
    nombre = str(doc.get("nombre") or "").strip()
    if nombre and not looks_like_size_label(nombre):
        return sanitize_download_filename(nombre, uuid)
    return f"{uuid}.download"


def commit_downloaded_file(
    docs_dir: Path,
    doc: dict,
    downloaded: Path,
    server_filename: str | None,
) -> Path:
    """Renombra al nombre Alfresco y actualiza manifest."""
    uuid = str(doc.get("uuid", "")).strip()
    chosen_name = ""
    if server_filename and not looks_like_size_label(server_filename):
        chosen_name = sanitize_download_filename(server_filename, uuid)
    elif downloaded.name.endswith(".download"):
        chosen_name = sanitize_download_filename(
            f"{uuid}.pdf",
            uuid,
        )
    else:
        chosen_name = downloaded.name

    target = docs_dir / chosen_name
    if downloaded.resolve() != target.resolve():
        if target.exists() and target.resolve() != downloaded.resolve():
            target = allocate_unique_path(docs_dir, chosen_name)
        downloaded.rename(target)
    else:
        target = downloaded

    doc["archivo"] = target.name
    doc["nombre"] = (
        server_filename
        if server_filename and not looks_like_size_label(server_filename)
        else target.name
    )
    return target


def download_and_store_document(
    docs_dir: Path,
    doc: dict,
    *,
    guest: bool,
    http_proxy: str | None,
) -> bool:
    """Descarga si falta. Retorna True si hubo descarga nueva."""
    from .downloader import download_file

    dest, exists = prepare_download_dest(docs_dir, doc)
    if exists:
        sync_doc_from_existing(docs_dir, doc)
        return False
    downloaded, server_filename = download_file(
        doc["uuid"],
        dest,
        guest=guest,
        http_proxy=http_proxy,
    )
    commit_downloaded_file(docs_dir, doc, downloaded, server_filename)
    return True


def prefer_canonical_archivo(docs_dir: Path, doc: dict) -> None:
    """Apunta manifest al archivo existente y elimina copias numeradas (_2, _3)."""
    existing = resolve_existing_download(docs_dir, doc)
    if existing is None or existing.stat().st_size == 0:
        return
    canonical_name = existing.name
    doc["archivo"] = canonical_name
    parsed_nombre = str(doc.get("nombre") or "").strip()
    if looks_like_size_label(parsed_nombre) or not parsed_nombre:
        doc["nombre"] = canonical_name

    stem, ext = Path(canonical_name).stem, Path(canonical_name).suffix
    for path in docs_dir.glob(f"{stem}_*{ext}"):
        if path.name == canonical_name or not path.is_file():
            continue
        path.unlink()


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
        if looks_like_size_label(str(nombre)):
            doc["archivo"] = current.name
            doc["nombre"] = current.name
            continue
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
    existing = resolve_existing_download(docs_dir, doc)
    if existing is not None:
        doc["archivo"] = existing.name
        sync_doc_from_existing(docs_dir, doc)
        return existing, True

    dest = allocate_unique_path(
        docs_dir, provisional_download_filename(doc)
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
