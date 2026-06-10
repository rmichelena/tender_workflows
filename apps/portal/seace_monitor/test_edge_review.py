"""Tests para hallazgos REVIEW_EDGE (storage, settings, proxy, UI)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from .config import AppConfig
from .db.models import AnalysisResult, FeedItem, ProcessStatus
from .downloader import download_file, filename_from_content_disposition
from .entity_process_cleanup import apply_removed_entity_policy
from .process_storage import archive_analyzed_process
from .scan_options import RemovedEntityPolicy
from .web.seace_proxy import PROXY_ROOT, _rewrite_text
from .web.settings_entities import EntitiesSaveRequest, _validate_save_request


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path, tenant_id="default")


def _proc(
    status: ProcessStatus,
    *,
    data_dir: str | None = None,
    analysis_status: str | None = None,
) -> FeedItem:
    proc = FeedItem(
        entity_id=1,
        anio=2026,
        nid_proceso="123",
        nomenclatura="TEST-1",
        status=status,
        data_dir=data_dir,
    )
    proc.id = 42
    if analysis_status is not None:
        proc.__dict__["analysis"] = AnalysisResult(status=analysis_status)
    return proc


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.flushed = False

    def query(self, _model):
        return _FakeQuery(self._rows)

    def flush(self):
        self.flushed = True


def test_apply_removed_entity_policy_defers_descargando(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc = _proc(ProcessStatus.descargando, data_dir=str(tmp_path / "x"))
    session = _FakeSession([proc])

    result = apply_removed_entity_policy(
        session, cfg, [1], RemovedEntityPolicy.discard_all
    )

    assert result.affected == 0
    assert result.deferred == 1
    assert proc.status == ProcessStatus.descargando


def test_apply_removed_entity_policy_defers_running_analysis(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc = _proc(
        ProcessStatus.descargada,
        data_dir=str(tmp_path / "x"),
        analysis_status="running",
    )
    session = _FakeSession([proc])

    result = apply_removed_entity_policy(
        session, cfg, [1], RemovedEntityPolicy.keep_analyzed
    )

    assert result.affected == 0
    assert result.deferred == 1


def test_archive_analyzed_process_idempotent_for_archivada(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = tmp_path / "tenants" / "default" / "trash" / "123_TEST"
    proc_dir.mkdir(parents=True)
    proc = _proc(ProcessStatus.archivada, data_dir=str(proc_dir))

    archive_analyzed_process(cfg, proc, MagicMock())

    assert proc.status == ProcessStatus.archivada
    assert proc_dir.is_dir()


def test_validate_save_request_rejects_invalid_date_before_mutations():
    entities = [
        MagicMock(ruc="20100000001", id=1, activa=False),
    ]
    body = EntitiesSaveRequest(
        selected_rucs=["20100000001"],
        added_scan_mode="since_date",
        since_date="31/02/25",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_save_request(
            body,
            entities=entities,
            selected={"20100000001"},
            added_entities=[entities[0]],
        )
    assert exc.value.status_code == 400
    assert "31/02/25" in str(exc.value.detail)


def test_rewrite_text_css_url_without_quotes():
    css = "background: url(/seacebus-uiwd-pub/resources/icon.png);"
    out = _rewrite_text(css)
    assert f"url({PROXY_ROOT}/resources/icon.png)" in out


def test_download_file_retries_then_succeeds(tmp_path: Path):
    dest = tmp_path / "doc.pdf"
    calls = {"n": 0}

    def fake_once(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        dest.write_bytes(b"ok")
        return dest, "doc.pdf"

    with patch("seace_monitor.downloader._download_file_once", side_effect=fake_once):
        result, server_name = download_file("uuid", dest)
    assert result == dest
    assert server_name == "doc.pdf"
    assert calls["n"] == 2


def test_filename_from_content_disposition_utf8():
    header = "attachment; filename*=UTF-8''Bases_LPA%2B0012026F_20260518_123248_485.pdf"
    assert (
        filename_from_content_disposition(header)
        == "Bases_LPA+0012026F_20260518_123248_485.pdf"
    )
