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
