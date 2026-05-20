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

    if args.command == "web":
        cfg = AppConfig.load(args.config)
        from .web.app import create_app

        app = create_app(cfg)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
