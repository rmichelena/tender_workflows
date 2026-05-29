"""Archivo de análisis previos al re-analizar tras cambios en watchlist."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from ..db.models import AnalysisResult, utcnow

logger = logging.getLogger(__name__)

_HISTORY_FIELDS = (
    "status",
    "error_message",
    "alcance",
    "incluye",
    "requisitos",
    "entregables",
    "equipos",
    "raw_json",
    "finished_at",
    "run_id",
)


def archive_analysis_before_rerun(
    proc_dir: Path, analysis: AnalysisResult
) -> Path | None:
    """Guarda snapshot del análisis terminado antes de un re-run."""
    if analysis.status != "done":
        return None
    hist_root = proc_dir / "fast_analysis" / "history"
    hist_root.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest_dir = hist_root / f"{stamp}_{uuid.uuid4().hex[:8]}"
    dest_dir.mkdir(parents=True, exist_ok=False)

    payload = {field: getattr(analysis, field) for field in _HISTORY_FIELDS}
    payload["archived_at"] = utcnow().isoformat()
    (dest_dir / "analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    summary = proc_dir / "free_reader_summary.md"
    if summary.is_file():
        shutil.copy2(summary, dest_dir / "free_reader_summary.md")

    from ..web.detail_data import analyzed_files_path

    analyzed = analyzed_files_path(proc_dir)
    if analyzed.is_file():
        shutil.copy2(analyzed, dest_dir / analyzed.name)

    logger.info("Análisis archivado en %s", dest_dir)
    return dest_dir
