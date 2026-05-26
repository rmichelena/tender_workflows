"""Reparación masiva de nombres de documento en BD y disco."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Process
from .document_storage import (
    download_and_store_document,
    looks_like_size_label,
    normalize_legacy_filenames,
    prefer_canonical_archivo,
    resolve_existing_download,
    write_manifest,
)
from .watchlist import _repair_document_metadata

logger = logging.getLogger(__name__)


def cleanup_size_label_orphans(docs_dir: Path, docs: list[dict]) -> int:
    """Elimina archivos huérfanos cuyo nombre es solo un tamaño (2646 KB)."""
    keep: set[Path] = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        path = resolve_existing_download(docs_dir, doc)
        if path is not None:
            keep.add(path.resolve())
    removed = 0
    for path in docs_dir.iterdir():
        if not path.is_file() or path.name == "manifest.json":
            continue
        if path.resolve() in keep:
            continue
        if not looks_like_size_label(path.stem):
            continue
        path.unlink()
        removed += 1
    return removed


def _doc_has_bad_name(doc: dict) -> bool:
    nombre = str(doc.get("nombre", "") or "")
    archivo = str(doc.get("archivo", "") or "")
    return looks_like_size_label(nombre) or looks_like_size_label(Path(archivo).stem)


def _redownload_bad_named_doc(
    docs_dir: Path, doc: dict, *, http_proxy: str | None
) -> bool:
    uuid = str(doc.get("uuid", "")).strip()
    if not uuid:
        return False
    existing = resolve_existing_download(docs_dir, doc)
    if existing is not None and not looks_like_size_label(existing.stem):
        return False
    if existing is not None and looks_like_size_label(existing.stem):
        existing.unlink()
    doc.pop("archivo", None)
    if _doc_has_bad_name(doc):
        doc["nombre"] = ""
    guest = str(doc.get("tipo_descarga", "3")).strip() != "3"
    download_and_store_document(
        docs_dir,
        doc,
        guest=guest,
        http_proxy=http_proxy,
    )
    return True


def repair_process_documents(process: Process, *, http_proxy: str | None = None) -> dict:
    stats = {"repaired": False, "redownloaded": 0, "removed_orphans": 0}
    if not process.data_dir:
        return stats
    docs_dir = Path(process.data_dir) / "documentos"
    if not docs_dir.is_dir():
        return stats
    stats["repaired"] = _repair_document_metadata(process)
    try:
        import json

        docs = json.loads(process.documentos_json or "[]")
        if not isinstance(docs, list):
            return stats
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if _doc_has_bad_name(doc):
                if _redownload_bad_named_doc(docs_dir, doc, http_proxy=http_proxy):
                    stats["redownloaded"] += 1
                    stats["repaired"] = True
        normalize_legacy_filenames(docs_dir, docs)
        for doc in docs:
            if isinstance(doc, dict):
                prefer_canonical_archivo(docs_dir, doc)
        stats["removed_orphans"] = cleanup_size_label_orphans(docs_dir, docs)
        if stats["repaired"] or stats["removed_orphans"]:
            write_manifest(docs_dir, docs)
            process.documentos_json = json.dumps(docs, ensure_ascii=False)
    except Exception:
        logger.exception("Cleanup huérfanos proceso id=%s", process.id)
    return stats


def repair_all_process_documents(session: Session, *, http_proxy: str | None = None) -> dict:
    totals = {
        "processes": 0,
        "repaired": 0,
        "redownloaded": 0,
        "removed_orphans": 0,
    }
    for proc in session.query(Process).filter(Process.data_dir.isnot(None)).all():
        totals["processes"] += 1
        stats = repair_process_documents(proc, http_proxy=http_proxy)
        if stats["repaired"]:
            totals["repaired"] += 1
        totals["redownloaded"] += stats["redownloaded"]
        totals["removed_orphans"] += stats["removed_orphans"]
    return totals


def main() -> None:
    import sys

    from .db.session import init_db, session_factory

    cfg = AppConfig.load()
    init_db(cfg.database_url)
    session = session_factory()
    try:
        totals = repair_all_process_documents(session, http_proxy=cfg.http_proxy)
        session.commit()
        print(
            f"Procesos revisados: {totals['processes']}, "
            f"metadata reparada: {totals['repaired']}, "
            f"re-descargados: {totals['redownloaded']}, "
            f"huérfanos eliminados: {totals['removed_orphans']}",
            file=sys.stderr,
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
