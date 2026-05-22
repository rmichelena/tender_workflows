"""Tests para borrado de carpetas de procesos."""

from pathlib import Path

from .config import AppConfig
from .db.models import Process, ProcessStatus
from .process_storage import (
    delete_process_data_dir,
    discard_process_downloads,
    purge_all_stale_process_data,
    resolve_restore_status,
)


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path, tenant_id="default")


def _proc_dir(tmp_path: Path, name: str = "123_TEST") -> Path:
    return tmp_path / "tenants" / "default" / "procesos" / name


def _proc(data_dir: str | None, status: ProcessStatus = ProcessStatus.descartada) -> Process:
    p = Process(
        entity_id=1,
        anio=2026,
        nid_proceso="123",
        nomenclatura="TEST-1",
        status=status,
    )
    p.id = 1
    p.data_dir = data_dir
    return p


def test_delete_process_data_dir(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    (proc_dir / "documentos" / "a.pdf").parent.mkdir(parents=True)
    (proc_dir / "documentos" / "a.pdf").write_bytes(b"x")

    proc = _proc(str(proc_dir))
    assert delete_process_data_dir(cfg, proc) is True
    assert proc.data_dir is None
    assert not proc_dir.exists()


def test_delete_rejects_path_outside_procesos(tmp_path: Path):
    cfg = _cfg(tmp_path)
    outside = tmp_path / "other" / "secret"
    outside.mkdir(parents=True)
    proc = _proc(str(outside))
    assert delete_process_data_dir(cfg, proc) is False
    assert outside.exists()


def test_discard_clears_metadata(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    proc = _proc(str(proc_dir))
    proc.documentos_json = '[{"uuid":"x","nombre":"a.pdf"}]'

    class FakeSession:
        def delete(self, _obj):
            pass

    discard_process_downloads(cfg, proc, FakeSession())
    assert proc.data_dir is None
    assert proc.documentos_json is None
    assert not proc_dir.exists()


def test_purge_orphans_and_descartada(tmp_path: Path):
    cfg = _cfg(tmp_path)
    root = _proc_dir(tmp_path).parent
    keep = root / "111_KEEP"
    orphan = root / "999_ORPHAN"
    stale = root / "222_STALE"
    for d in (keep, orphan, stale):
        d.mkdir(parents=True)

    class FakeSession:
        def query(self, _model):
            return self

        def all(self):
            keep_proc = _proc(str(keep), ProcessStatus.descargada)
            stale_proc = _proc(str(stale), ProcessStatus.descartada)
            stale_proc.documentos_json = '[{"uuid":"x","nombre":"a.pdf"}]'
            return [keep_proc, stale_proc]

    session = FakeSession()
    db_cleaned, orphans = purge_all_stale_process_data(cfg, session)
    assert db_cleaned == 1
    assert orphans == 1
    assert not stale.exists()
    assert not orphan.exists()
    assert keep.exists()
    for proc in session.all():
        if proc.status == ProcessStatus.descartada:
            assert proc.documentos_json is None


def test_resolve_restore_status_without_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc = _proc(None, ProcessStatus.descartada)
    proc.analysis = type("A", (), {"status": "done"})()
    assert resolve_restore_status(cfg, proc) == ProcessStatus.publicada


def test_resolve_restore_status_with_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    proc = _proc(str(proc_dir), ProcessStatus.descartada)
    assert resolve_restore_status(cfg, proc) == ProcessStatus.descargada
    proc.analysis = type("A", (), {"status": "done"})()
    assert resolve_restore_status(cfg, proc) == ProcessStatus.analizada
