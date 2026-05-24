"""Worker de escaneo periódico."""

from __future__ import annotations

import logging
import sys
import time

from .config import AppConfig
from .db.models import Entity
from .db.session import init_db, session_factory
from .entity_catalog import sync_entity_catalog_if_changed
from .scanner import MultiEntityScanner
from .tenant_paths import migrate_legacy_layout, migrate_process_data_dir_refs

logger = logging.getLogger(__name__)

_CATALOG_SYNC_RETRIES = 3
_CATALOG_SYNC_BACKOFF_SECONDS = 5


def _bootstrap_catalog(session, cfg: AppConfig) -> None:
    active_before = session.query(Entity).filter(Entity.activa.is_(True)).count()
    last_error: Exception | None = None
    for attempt in range(1, _CATALOG_SYNC_RETRIES + 1):
        try:
            sync_entity_catalog_if_changed(session, cfg)
            session.commit()
            return
        except Exception as exc:
            session.rollback()
            last_error = exc
            logger.exception(
                "Sync catálogo OSCE falló (intento %s/%s)",
                attempt,
                _CATALOG_SYNC_RETRIES,
            )
            if attempt < _CATALOG_SYNC_RETRIES:
                time.sleep(_CATALOG_SYNC_BACKOFF_SECONDS * attempt)

    active_after = session.query(Entity).filter(Entity.activa.is_(True)).count()
    if active_before == 0 and active_after == 0:
        raise SystemExit(
            "Catálogo OSCE no disponible y sin entidades activas; "
            "worker no puede escanear."
        ) from last_error
    logger.error(
        "Sync catálogo OSCE falló tras %s intentos; se continúa con %s entidad(es) activa(s)",
        _CATALOG_SYNC_RETRIES,
        active_after,
    )


def run_worker(config: AppConfig | None = None, once: bool = False) -> None:
    cfg = config or AppConfig.load()
    init_db(cfg.database_url)
    interval = cfg.poll_interval_seconds

    logger.info(
        "Worker iniciado — intervalo %s (%ss), tenant: %s",
        cfg.poll_interval,
        interval,
        cfg.tenant_id,
    )
    if migrate_legacy_layout(cfg):
        logger.info("Layout de datos migrado a tenants/%s/", cfg.tenant_id)

    session = session_factory()
    try:
        path_updates = migrate_process_data_dir_refs(session, cfg)
        if path_updates:
            session.commit()
            logger.info(
                "Actualizadas %s rutas data_dir tras migración de layout",
                path_updates,
            )
        _bootstrap_catalog(session, cfg)
    finally:
        session.close()

    while True:
        session = session_factory()
        try:
            scanner = MultiEntityScanner(cfg, session)
            n = scanner.run_once()
            session.commit()
            logger.info("Ciclo completado: %s proceso(s) nuevo(s)", n)
        except Exception:
            session.rollback()
            logger.exception("Error en ciclo de escaneo")
        finally:
            session.close()

        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    once = "--once" in sys.argv
    run_worker(once=once)
