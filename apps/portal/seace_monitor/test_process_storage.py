"""Tests para borrado de carpetas de procesos."""

from pathlib import Path

from .config import AppConfig
from .db.models import AnalysisResult, Process, ProcessStatus
from .document_storage import (
    prefer_canonical_archivo,
    prepare_download_dest,
    write_manifest,
)
from .process_storage import (
    archive_analyzed_process,
    delete_process_data_dir,
    discard_process_downloads,
    purge_all_stale_process_data,
    restore_archived_process,
    resolve_restore_status,
)
from .tenant_paths import trash_root


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


def _with_analysis(proc: Process, status: str = "done") -> Process:
    proc.__dict__["analysis"] = AnalysisResult(status=status)
    return proc


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
    proc = _proc(str(proc_dir), ProcessStatus.descargada)
    proc.documentos_json = '[{"uuid":"x","nombre":"a.pdf"}]'

    class FakeSession:
        def delete(self, _obj):
            pass

        def flush(self):
            pass

    discard_process_downloads(cfg, proc, FakeSession())
    assert proc.data_dir is None
    assert proc.documentos_json is None
    assert not proc_dir.exists()


def test_archive_moves_to_trash_and_keeps_analysis(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    (proc_dir / "documentos").mkdir()
    (proc_dir / "documentos" / "a.pdf").write_bytes(b"x")
    proc = _proc(str(proc_dir), ProcessStatus.analizada)
    proc.documentos_json = '[{"uuid":"x","nombre":"a.pdf"}]'
    _with_analysis(proc)

    archive_analyzed_process(cfg, proc)

    assert proc.status == ProcessStatus.archivada
    assert proc.documentos_json is not None
    assert proc.__dict__["analysis"] is not None
    assert not proc_dir.exists()
    trash_path = trash_root(cfg) / proc_dir.name
    assert trash_path.is_dir()
    assert (trash_path / "documentos" / "a.pdf").is_file()
    assert proc.data_dir == str(trash_path.resolve())


def test_restore_archived_moves_back_to_procesos(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    trash_dir = trash_root(cfg) / proc_dir.name
    trash_dir.mkdir(parents=True)
    (trash_dir / "documentos").mkdir()
    proc = _proc(str(trash_dir), ProcessStatus.archivada)
    _with_analysis(proc)

    restore_archived_process(cfg, proc)

    assert proc.status == ProcessStatus.analizada
    assert proc_dir.is_dir()
    assert not trash_dir.exists()
    assert proc.data_dir == str(proc_dir.resolve())


def test_purge_orphans_and_descartada(tmp_path: Path):
    cfg = _cfg(tmp_path)
    root = _proc_dir(tmp_path).parent
    keep = root / "111_KEEP"
    orphan = root / "999_ORPHAN"
    stale = root / "222_STALE"
    for d in (keep, orphan, stale):
        d.mkdir(parents=True)

    keep_proc = _proc(str(keep), ProcessStatus.descargada)
    stale_proc = _proc(str(stale), ProcessStatus.descartada)
    stale_proc.documentos_json = '[{"uuid":"x","nombre":"a.pdf"}]'

    class FakeSession:
        def __init__(self):
            self._rows = [keep_proc, stale_proc]

        def query(self, _model):
            return self

        def all(self):
            return self._rows

    session = FakeSession()
    db_cleaned, orphans = purge_all_stale_process_data(cfg, session)
    assert db_cleaned == 1
    assert orphans == 1
    assert not stale.exists()
    assert not orphan.exists()
    assert keep.exists()
    assert stale_proc.documentos_json is None


def test_archive_collision_uses_unique_suffix(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    trash = trash_root(cfg)
    trash.mkdir(parents=True)
    (trash / proc_dir.name).mkdir()
    (trash / f"1_{proc_dir.name}").mkdir()
    proc = _proc(str(proc_dir), ProcessStatus.analizada)

    archive_analyzed_process(cfg, proc)

    dest = Path(proc.data_dir)
    assert dest.is_dir()
    assert dest.name.startswith(f"1_{proc_dir.name}_")
    assert not proc_dir.exists()


def test_resolve_restore_status_without_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc = _proc(None, ProcessStatus.descartada)
    _with_analysis(proc)
    assert resolve_restore_status(cfg, proc) == ProcessStatus.publicada


def test_resolve_restore_status_with_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    proc_dir = _proc_dir(tmp_path)
    proc_dir.mkdir(parents=True)
    proc = _proc(str(proc_dir), ProcessStatus.descartada)
    assert resolve_restore_status(cfg, proc) == ProcessStatus.descargada
    _with_analysis(proc)
    assert resolve_restore_status(cfg, proc) == ProcessStatus.analizada


def test_prepare_download_dest_finds_canonical_without_manifest_archivo(tmp_path: Path):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    doc = {
        "uuid": "85b414d9-b722-41dd-ab61-f5dac6490b05",
        "nombre": "Bases_LPA 0012026F.pdf",
    }
    canonical = docs_dir / "Bases_LPA 0012026F.pdf"
    canonical.write_bytes(b"x" * 100)
    (docs_dir / "Bases_LPA 0012026F_3.pdf").write_bytes(b"x" * 100)

    dest, exists = prepare_download_dest(docs_dir, doc)

    assert exists is True
    assert dest == canonical
    prefer_canonical_archivo(docs_dir, doc)
    assert doc["archivo"] == canonical.name
    assert not (docs_dir / "Bases_LPA 0012026F_3.pdf").exists()
    write_manifest(docs_dir, [doc])
    dest2, exists2 = prepare_download_dest(docs_dir, doc)
    assert exists2 is True
    assert dest2 == canonical
