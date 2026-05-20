"""Bucle de monitoreo y persistencia de estado."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import ProcessRow, SeaceClient
from .downloader import download_file
from .parser import FichaData, parse_ficha, row_snapshot_hash

logger = logging.getLogger(__name__)


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"processes": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def process_slug(row: ProcessRow) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in row.nomenclatura)
    return f"{row.nid_proceso}_{safe}"[:120]


class SeaceMonitor:
    def __init__(
        self,
        client: SeaceClient,
        data_dir: Path,
        state_file: Path,
        max_pages: int = 1,
        download_documents: bool = True,
    ) -> None:
        self.client = client
        self.data_dir = data_dir
        self.state_file = state_file
        self.max_pages = max_pages
        self.download_documents = download_documents
        self.state = load_state(state_file)

    def run_once(self) -> list[str]:
        """Un ciclo de revisión. Devuelve IDs de procesos nuevos o actualizados."""
        changed: list[str] = []
        processes: dict[str, Any] = self.state.setdefault("processes", {})

        for page in range(self.max_pages):
            _, soup = self.client.fetch_list_page(page)
            rows = self.client.parse_rows(soup)
            logger.info("Página %s: %s procesos", page + 1, len(rows))

            for row in rows:
                if not row.nid_proceso:
                    continue
                list_hash = row_snapshot_hash(row)
                prev = processes.get(row.nid_proceso)
                is_new = prev is None
                list_changed = prev and prev.get("list_hash") != list_hash

                if is_new or list_changed:
                    try:
                        event = self._handle_process(row, is_new=is_new)
                        changed.append(event)
                    except Exception:
                        logger.exception(
                            "Error procesando %s (%s)",
                            row.nomenclatura,
                            row.nid_proceso,
                        )
                else:
                    processes[row.nid_proceso]["last_checked"] = _now_iso()

        save_state(self.state_file, self.state)
        return changed

    def _handle_process(self, row: ProcessRow, is_new: bool) -> str:
        logger.info(
            "%s proceso %s — %s",
            "Nuevo" if is_new else "Actualizado",
            row.nid_proceso,
            row.nomenclatura,
        )

        ficha_result = self.client.open_ficha(row)
        ficha = parse_ficha(
            ficha_result.html, ficha_result.ficha_id, row.nid_proceso
        )

        proc_dir = self.data_dir / "procesos" / process_slug(row)
        proc_dir.mkdir(parents=True, exist_ok=True)

        meta_path = proc_dir / "ficha.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "listado": asdict(row),
                    "ficha": {
                        **asdict(ficha),
                        "cronograma": [asdict(c) for c in ficha.cronograma],
                        "documentos": [asdict(d) for d in ficha.documentos],
                    },
                    "ficha_url": ficha_result.url,
                    "captured_at": _now_iso(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        downloaded: list[str] = []
        if self.download_documents:
            docs_dir = proc_dir / "documentos"
            for doc in ficha.documentos:
                ext = Path(doc.nombre).suffix or ".pdf"
                dest = docs_dir / f"{doc.uuid}{ext}"
                if dest.exists():
                    downloaded.append(doc.uuid)
                    continue
                try:
                    download_file(doc.uuid, dest, guest=doc.tipo_descarga != "3")
                    logger.info("Descargado: %s", doc.nombre)
                    downloaded.append(doc.uuid)
                except Exception:
                    logger.exception("No se pudo descargar %s", doc.nombre)

        processes = self.state.setdefault("processes", {})
        processes[row.nid_proceso] = {
            "nid_proceso": row.nid_proceso,
            "nomenclatura": row.nomenclatura,
            "ficha_id": ficha.ficha_id,
            "list_hash": row_snapshot_hash(row),
            "content_hash": ficha.content_hash(),
            "first_seen": processes.get(row.nid_proceso, {}).get("first_seen")
            or _now_iso(),
            "last_seen": _now_iso(),
            "last_checked": _now_iso(),
            "downloaded_docs": downloaded,
            "data_dir": str(proc_dir),
        }

        return row.nid_proceso

    def run_forever(self, interval_seconds: int) -> None:
        logger.info(
            "Monitoreo iniciado (cada %ss, %s página(s))",
            interval_seconds,
            self.max_pages,
        )
        while True:
            try:
                changed = self.run_once()
                if changed:
                    logger.info("Cambios detectados: %s", ", ".join(changed))
            except Exception:
                logger.exception("Error en ciclo de monitoreo")
            time.sleep(interval_seconds)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
