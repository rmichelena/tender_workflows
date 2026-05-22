"""Tests para rutas multi-tenant."""

from pathlib import Path

from .config import AppConfig
from .tenant_paths import migrate_legacy_layout, procesos_root, tenant_root


def test_procesos_root_default_tenant(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, tenant_id="default")
    assert procesos_root(cfg) == (tmp_path / "tenants" / "default" / "procesos").resolve()


def test_migrate_legacy_layout(tmp_path: Path):
    legacy = tmp_path / "procesos" / "123_TEST"
    legacy.mkdir(parents=True)
    (legacy / "marker.txt").write_text("x", encoding="utf-8")

    cfg = AppConfig(data_dir=tmp_path, tenant_id="default")
    assert migrate_legacy_layout(cfg) is True

    target = tmp_path / "tenants" / "default" / "procesos" / "123_TEST"
    assert target.is_dir()
    assert (target / "marker.txt").read_text() == "x"
    assert not (tmp_path / "procesos").exists()
    assert migrate_legacy_layout(cfg) is False


def test_migrate_skips_when_tenant_already_populated(tmp_path: Path):
    legacy = tmp_path / "procesos" / "old"
    legacy.mkdir(parents=True)
    existing = tmp_path / "tenants" / "default" / "procesos" / "new"
    existing.mkdir(parents=True)

    cfg = AppConfig(data_dir=tmp_path, tenant_id="default")
    assert migrate_legacy_layout(cfg) is False
    assert legacy.is_dir()
    assert existing.is_dir()
