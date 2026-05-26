"""Reparación masiva de nombres de documento en BD y disco."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .db.models import Process
from .document_storage import (
    looks_like_size_label,
    resolve_existing_download,
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


def repair_process_documents(process: Process) -> dict:
    stats = {"repaired": False, "removed_orphans": 0}
    if not process.data_dir:
        return stats
    docs_dir = Path(process.data_dir) / "documentos"
    if not docs_dir.is_dir():
        return stats
    stats["repaired"] = _repair_document_metadata(process)
    try:
        import json

        docs = json.loads(process.documentos_json or "[]")
        if isinstance(docs, list):
            stats["removed_orphans"] = cleanup_size_label_orphans(docs_dir, docs)
            if stats["removed_orphans"] or stats["repaired"]:
                from .document_storage import write_manifest

                write_manifest(docs_dir, docs)
    except Exception:
        logger.exception("Cleanup huérfanos proceso id=%s", process.id)
    return stats


def repair_all_process_documents(session: Session) -> dict:
    totals = {"processes": 0, "repaired": 0, "removed_orphans": 0}
    for proc in session.query(Process).filter(Process.data_dir.isnot(None)).all():
        totals["processes"] += 1
        stats = repair_process_documents(proc)
        if stats["repaired"]:
            totals["repaired"] += 1
        totals["removed_orphans"] += stats["removed_orphans"]
    return totals


def main() -> None:
    import sys

    from .config import AppConfig
    from .db.session import init_db, session_factory

    cfg = AppConfig.load()
    init_db(cfg.database_url)
    session = session_factory()
    try:
        totals = repair_all_process_documents(session)
        session.commit()
        print(
            f"Procesos revisados: {totals['processes']}, "
            f"metadata reparada: {totals['repaired']}, "
            f"huérfanos eliminados: {totals['removed_orphans']}",
            file=sys.stderr,
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
