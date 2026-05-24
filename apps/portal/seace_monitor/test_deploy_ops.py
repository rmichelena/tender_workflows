"""Tests deploy-related: DB engine y worker heartbeat."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import create_engine

from seace_monitor.db.session import init_db
from seace_monitor.worker_heartbeat import (
    write_worker_heartbeat,
    worker_heartbeat_ok,
)


def test_postgres_create_engine_without_connect_args_none():
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if connect_args:
        engine_kwargs["connect_args"] = connect_args
    engine = create_engine(
        "postgresql+psycopg://u:p@localhost:5432/test",
        **engine_kwargs,
    )
    assert engine is not None


def test_init_db_sqlite(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(f"sqlite:///{db}")
    assert db.exists()


def test_worker_heartbeat_ok_within_stale_window(tmp_path: Path):
    write_worker_heartbeat(tmp_path, poll_interval_seconds=3600)
    assert worker_heartbeat_ok(tmp_path)


def test_worker_heartbeat_fails_when_stale(tmp_path: Path):
    write_worker_heartbeat(tmp_path, poll_interval_seconds=60)
    path = tmp_path / "worker_heartbeat.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ts"] = time.time() - payload["max_stale_seconds"] - 10
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert not worker_heartbeat_ok(tmp_path)
