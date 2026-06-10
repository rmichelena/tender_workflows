"""Rutas por tenant bajo data_dir (multi-usuario ready)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"


def tenant_root(config: AppConfig) -> Path:
    return (config.data_dir / "tenants" / config.tenant_id).resolve()


def trash_root(config: AppConfig) -> Path:
    return (tenant_root(config) / "trash").resolve()


def procesos_root(config: AppConfig) -> Path:
    return (tenant_root(config) / "procesos").resolve()


def tenant_settings_dir(config: AppConfig) -> Path:
    return tenant_root(config) / "settings"


def tenant_seace_dir(config: AppConfig) -> Path:
    return tenant_root(config) / "seace"


def tenant_agent_dir(config: AppConfig) -> Path:
    return tenant_root(config) / "agent"


def ensure_tenant_layout(config: AppConfig) -> None:
    """Crea subdirectorios del tenant activo."""
    for path in (
        tenant_settings_dir(config),
        tenant_seace_dir(config),
        procesos_root(config),
        trash_root(config),
        tenant_agent_dir(config),
    ):
        path.mkdir(parents=True, exist_ok=True)


def migrate_legacy_layout(config: AppConfig) -> bool:
    """Mueve data/procesos/ → data/tenants/{tenant_id}/procesos/ si aplica."""
    legacy = (config.data_dir / "procesos").resolve()
    target = procesos_root(config)

    if not legacy.is_dir():
        ensure_tenant_layout(config)
        return False

    try:
        legacy.relative_to(target)
        ensure_tenant_layout(config)
        return False
    except ValueError:
        pass

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and any(target.iterdir()):
        logger.warning(
            "Layout legacy %s ignorado: ya hay datos en %s",
            legacy,
            target,
        )
        ensure_tenant_layout(config)
        return False

    if target.exists():
        target.rmdir()

    shutil.move(str(legacy), str(target))
    logger.info("Migrado layout legacy %s → %s", legacy, target)
    ensure_tenant_layout(config)
    return True


def legacy_procesos_root(config: AppConfig) -> Path:
    return (config.data_dir / "procesos").resolve()


def remap_process_data_dir(config: AppConfig, data_dir: str | None) -> str | None:
    """Convierte rutas legacy data/procesos/ → tenants/{id}/procesos/."""
    if not data_dir:
        return None
    path = Path(data_dir).resolve()
    legacy_root = legacy_procesos_root(config)
    new_root = procesos_root(config)
    try:
        rel = path.relative_to(legacy_root)
    except ValueError:
        return data_dir
    return str((new_root / rel).resolve())


def migrate_process_data_dir_refs(session, config: AppConfig) -> int:
    """Actualiza data_dir en BD tras migración de carpetas."""
    from .db.models import FeedItem

    updated = 0
    for proc in session.query(FeedItem).filter(FeedItem.data_dir.isnot(None)):
        new_path = remap_process_data_dir(config, proc.data_dir)
        if new_path and new_path != proc.data_dir:
            proc.data_dir = new_path
            updated += 1
    return updated
