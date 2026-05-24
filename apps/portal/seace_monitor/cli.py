"""CLI unificada."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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

    sync_ent = sub.add_parser(
        "sync-entities",
        help="Sincronizar catálogo oficial OSCE de entidades contratantes",
    )
    sync_ent.add_argument("-c", "--config", default="config.yaml")
    sync_ent.add_argument(
        "--force", action="store_true", help="Aplicar aunque el hash no haya cambiado"
    )
    sync_ent.add_argument("-v", "--verbose", action="store_true")

    web = sub.add_parser("web", help="Servidor web UI")
    web.add_argument("-c", "--config", default="config.yaml")
    web.add_argument("--host", default="0.0.0.0")
    web.add_argument("--port", type=int, default=8080)
    web.add_argument("-v", "--verbose", action="store_true")

    wh = sub.add_parser(
        "worker-healthcheck",
        help="Exit 0 si el heartbeat del worker es reciente (Docker healthcheck)",
    )
    wh.add_argument("-c", "--config", default="config.yaml")

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
            repair_archived_processes,
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
            archived = repair_archived_processes(cfg, session)
            discarded = repair_discarded_processes(cfg, session)
            session.commit()
            logging.info(
                "Limpieza completada: %s metadato(s) obsoleto(s), %s huérfana(s), "
                "%s inconsistente(s), %s archivado(s) reparado(s), %s descartado(s) reparado(s)",
                db_cleaned,
                orphans,
                repaired,
                archived,
                discarded,
            )
        finally:
            session.close()
        return 0

    if args.command == "sync-entities":
        from .db.session import init_db, session_factory
        from .entity_catalog import sync_entity_catalog_if_changed
        from .tenant_paths import migrate_legacy_layout

        cfg = AppConfig.load(args.config)
        migrate_legacy_layout(cfg)
        init_db(cfg.database_url)
        session = session_factory()
        try:
            result = sync_entity_catalog_if_changed(
                session, cfg, force=args.force
            )
            session.commit()
            if result is None:
                logging.info("Catálogo OSCE sin cambios")
            else:
                logging.info(
                    "Catálogo OSCE: +%s nuevas, %s actualizadas, %s Inactivo omitidas",
                    result.added,
                    result.updated,
                    result.skipped_inactivo,
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

    if args.command == "worker-healthcheck":
        from .worker_heartbeat import worker_heartbeat_ok

        cfg = AppConfig.load(args.config)
        return 0 if worker_heartbeat_ok(Path(cfg.data_dir)) else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
