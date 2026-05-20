"""Worker de escaneo periódico."""

from __future__ import annotations

import logging
import time

from .config import AppConfig
from .db.session import init_db, session_factory
from .scanner import MultiEntityScanner

logger = logging.getLogger(__name__)


def run_worker(config: AppConfig | None = None, once: bool = False) -> None:
    cfg = config or AppConfig.load()
    init_db(cfg.database_url)
    interval = cfg.poll_interval_seconds

    logger.info(
        "Worker iniciado — intervalo %s (%ss), entidades: %s",
        cfg.poll_interval,
        interval,
        cfg.entities_csv,
    )

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
    import sys

    once = "--once" in sys.argv
    run_worker(once=once)
