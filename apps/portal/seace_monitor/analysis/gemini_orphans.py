"""Registro local de recursos Gemini que no se pudieron borrar."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ORPHAN_LOG_NAME = "gemini_orphans.jsonl"


def orphan_log_path(proc_dir: Path) -> Path:
    return proc_dir / "fast_analysis" / ORPHAN_LOG_NAME


def log_gemini_orphan(
    proc_dir: Path, *, kind: str, resource_id: str, error: str = ""
) -> None:
    path = orphan_log_path(proc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "resource_id": resource_id,
        "error": error[:500],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
