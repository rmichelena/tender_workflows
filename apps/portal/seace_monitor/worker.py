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
from .ingest import get_adapter, registered_sources
from .ingest.base import SourceAdapter
from .worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger(__name__)


def _active_scan_adapters(cfg: AppConfig) -> list[SourceAdapter]:
    """Adapters con escaneo habilitado, ordenados por `scan_priority`.

    El worker es agnóstico a la fuente: agregar un canal = registrar su adapter,
    sin tocar este módulo.
    """
    adapters = [get_adapter(source) for source in registered_sources()]
    active = [
        adapter
        for adapter in adapters
        if adapter.capabilities.scan_listings and adapter.scan_enabled(cfg)
    ]
    return sorted(active, key=lambda adapter: (adapter.scan_priority, adapter.source))

_CATALOG_SYNC_RETRIES = 3
_CATALOG_SYNC_BACKOFF_SECONDS = 5
_worker_lock_fd = None


def seconds_until_next_wake(
    now: float, next_scan_at: float, next_watch_at: float
) -> float:
    """Segundos de sleep del worker (independiente de poll vs watchlist)."""
    return max(1.0, min(next_scan_at, next_watch_at) - now)


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
    poll_s = cfg.poll_interval_seconds

    adapters = _active_scan_adapters(cfg)

    logger.info(
        "Worker iniciado — fuentes: %s, tenant: %s",
        ", ".join(adapter.source for adapter in adapters) or "(ninguna)",
        cfg.tenant_id,
    )

    session = session_factory()
    try:
        _bootstrap_catalog(session, cfg)
    finally:
        session.close()

    write_worker_heartbeat(data_dir, poll_interval_seconds=poll_s)

    now = time.time()
    next_scan_at = {adapter.source: now for adapter in adapters}
    next_watch_at = {adapter.source: now for adapter in adapters}

    while True:
        now = time.time()
        session = session_factory()
        scan_counts: dict[str, int] = {}
        watch_counts: dict[str, int] = {}
        ran_any = False
        try:
            # Escaneos primero (en orden de prioridad), luego watchlists.
            for adapter in adapters:
                if now >= next_scan_at[adapter.source]:
                    scan_counts[adapter.source] = adapter.scan(cfg, session)
                    next_scan_at[adapter.source] = now + adapter.scan_interval_seconds(cfg)
                    ran_any = True
            for adapter in adapters:
                if now >= next_watch_at[adapter.source]:
                    watch_counts[adapter.source] = adapter.refresh_watchlist(cfg, session)
                    next_watch_at[adapter.source] = now + adapter.watch_interval_seconds(cfg)
                    ran_any = True
            if ran_any:
                session.commit()
            if any(scan_counts.values()) or any(watch_counts.values()):
                summary = "; ".join(
                    f"{source} {scan_counts.get(source, 0)} nuevo(s)/"
                    f"{watch_counts.get(source, 0)} watch"
                    for source in sorted(set(scan_counts) | set(watch_counts))
                )
                logger.info("Ciclo completado: %s", summary)
        except Exception:
            session.rollback()
            logger.exception("Error en ciclo de escaneo/watchlist")
        finally:
            session.close()

        write_worker_heartbeat(data_dir, poll_interval_seconds=poll_s)

        if once:
            break
        if next_scan_at:
            sleep_for = seconds_until_next_wake(
                time.time(),
                min(next_scan_at.values()),
                min(next_watch_at.values()),
            )
        else:
            sleep_for = float(poll_s)
        time.sleep(sleep_for)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    once = "--once" in sys.argv
    run_worker(once=once)
