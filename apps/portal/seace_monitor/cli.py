"""CLI unificada."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import AppConfig
from .worker import run_worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEACE Monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Ejecutar escaneo SEACE")
    scan.add_argument("--once", action="store_true")
    scan.add_argument("-c", "--config", default="config.yaml")
    scan.add_argument("-v", "--verbose", action="store_true")

    cleanup = sub.add_parser(
        "cleanup-data", help="Borrar carpetas de procesos descartados/huérfanas"
    )
    cleanup.add_argument("-c", "--config", default="config.yaml")
    cleanup.add_argument("-v", "--verbose", action="store_true")

    web = sub.add_parser("web", help="Servidor web UI")
    web.add_argument("-c", "--config", default="config.yaml")
    web.add_argument("--host", default="0.0.0.0")
    web.add_argument("--port", type=int, default=8080)
    web.add_argument("-v", "--verbose", action="store_true")

    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "scan":
        cfg = AppConfig.load(args.config)
        run_worker(cfg, once=args.once)
        return 0

    if args.command == "cleanup-data":
        from .db.session import init_db, session_factory
        from .process_storage import (
            purge_all_stale_process_data,
            repair_discarded_processes,
            repair_processes_missing_data,
        )
        from .tenant_paths import migrate_legacy_layout, migrate_process_data_dir_refs

        cfg = AppConfig.load(args.config)
        migrate_legacy_layout(cfg)
        init_db(cfg.database_url)
        session = session_factory()
        try:
            path_updates = migrate_process_data_dir_refs(session, cfg)
            if path_updates:
                session.commit()
            db_cleaned, orphans = purge_all_stale_process_data(cfg, session)
            repaired = repair_processes_missing_data(cfg, session)
            discarded = repair_discarded_processes(cfg, session)
            session.commit()
            logging.info(
                "Limpieza completada: %s metadato(s) obsoleto(s), %s huérfana(s), "
                "%s inconsistente(s), %s descartado(s) reparado(s)",
                db_cleaned,
                orphans,
                repaired,
                discarded,
            )
        finally:
            session.close()
        return 0

    if args.command == "web":
        cfg = AppConfig.load(args.config)
        from .web.app import create_app

        app = create_app(cfg)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
