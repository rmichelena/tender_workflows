"""Worker de escaneo periódico."""

from __future__ import annotations

import fcntl
import logging
import os
import sys
import time
from pathlib import Path

from .config import AppConfig
from .db.models import Entity
from .db.session import init_db, session_factory
from .entity_catalog import sync_entity_catalog_if_changed
from .scanner import MultiEntityScanner
from .watchlist import refresh_watchlist_processes
from .worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger(__name__)

_CATALOG_SYNC_RETRIES = 3
_CATALOG_SYNC_BACKOFF_SECONDS = 5
_worker_lock_fd = None


def _acquire_worker_lock(data_dir: Path) -> None:
    """Un solo worker por volumen SQLite/datos (evita --scale worker=2)."""
    global _worker_lock_fd
    lock_path = data_dir / ".worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _worker_lock_fd = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(_worker_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit(
            "Otro worker SEACE Monitor ya está en ejecución en este volumen de datos."
        ) from exc
    _worker_lock_fd.write(str(os.getpid()))
    _worker_lock_fd.flush()


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
    data_dir = Path(cfg.data_dir)
    _acquire_worker_lock(data_dir)
    init_db(cfg.database_url)
    interval = cfg.poll_interval_seconds

    logger.info(
        "Worker iniciado — intervalo %s (%ss), tenant: %s",
        cfg.poll_interval,
        interval,
        cfg.tenant_id,
    )

    session = session_factory()
    try:
        _bootstrap_catalog(session, cfg)
    finally:
        session.close()

    write_worker_heartbeat(data_dir, poll_interval_seconds=interval)

    while True:
        session = session_factory()
        try:
            scanner = MultiEntityScanner(cfg, session)
            n = scanner.run_once()
            w = refresh_watchlist_processes(cfg, session)
            session.commit()
            logger.info(
                "Ciclo completado: %s proceso(s) nuevo(s), %s watchlist actualizado(s)",
                n,
                w,
            )
        except Exception:
            session.rollback()
            logger.exception("Error en ciclo de escaneo")
        finally:
            session.close()

        write_worker_heartbeat(data_dir, poll_interval_seconds=interval)

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
