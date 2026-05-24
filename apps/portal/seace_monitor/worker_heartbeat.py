"""Heartbeat del worker para healthchecks Docker."""

from __future__ import annotations

import json
import time
from pathlib import Path

HEARTBEAT_NAME = "worker_heartbeat.json"


def heartbeat_path(data_dir: Path) -> Path:
    return Path(data_dir) / HEARTBEAT_NAME


def write_worker_heartbeat(data_dir: Path, *, poll_interval_seconds: int) -> None:
    path = heartbeat_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    max_stale = int(poll_interval_seconds * 2 + 1800)
    path.write_text(
        json.dumps({"ts": time.time(), "max_stale_seconds": max_stale}, indent=2) + "\n",
        encoding="utf-8",
    )


def worker_heartbeat_ok(data_dir: Path) -> bool:
    path = heartbeat_path(data_dir)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = float(data["ts"])
        max_stale = float(data["max_stale_seconds"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError, KeyError):
        return False
    return (time.time() - ts) <= max_stale
