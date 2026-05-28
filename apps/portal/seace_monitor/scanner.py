"""Escáner multi-entidad: listado + ficha (sin descargar documentos)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .auto_reject import AutoRejectRule, apply_auto_reject_rules, load_auto_reject_rules
from .client import ProcessRow, SeaceClient
from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus, utcnow
from .parser import extract_cronograma_fechas, parse_ficha, row_snapshot_hash
from .scan_options import ScanOptions, passes_date_filter

logger = logging.getLogger(__name__)

_FICHA_REFRESH_STATUSES = frozenset(
    {
        ProcessStatus.publicada,
        ProcessStatus.descargada,
    }
)


class MultiEntityScanner:
    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session
        self.auto_reject_rules: list[AutoRejectRule] = load_auto_reject_rules(config)

    def run_once(self, options: ScanOptions | None = None) -> int:
        opts = options or ScanOptions()
        entities = self._entities_to_scan(opts)
        new_count = 0

        for entity in entities:
            if not entity.activa and opts.entity_ids is None:
                continue
            try:
                n = self._scan_entity(entity, opts)
                new_count += n
                self.session.commit()
            except Exception:
                self.session.rollback()
                logger.exception(
                    "Error escaneando entidad %s (%s)", entity.nombre, entity.ruc
                )

        return new_count

    def _entities_to_scan(self, options: ScanOptions) -> list[Entity]:
        if options.entity_ids:
            return (
                self.session.query(Entity)
                .filter(Entity.id.in_(sorted(options.entity_ids)))
                .order_by(Entity.nombre)
                .all()
            )
        return (
            self.session.query(Entity)
            .filter(Entity.activa.is_(True))
            .order_by(Entity.nombre)
            .all()
        )

    def _scan_entity(self, entity: Entity, options: ScanOptions) -> int:
        client = SeaceClient(
            ruc_entidad=entity.ruc,
            anio=self.config.anio,
            rows_per_page=self.config.rows_per_page,
            http_proxy=self.config.http_proxy,
        )
        new_count = 0
        seen_nids: set[str] = set()

        first_soup = None
        max_pages = self.config.max_pages
        if options.multipage:
            _, first_soup = client.fetch_list_page(0)
            max_pages = min(
                client.total_pages(first_soup),
                options.max_pages_cap,
            )
            logger.info(
                "[%s] escaneo multipágina: %s página(s)", entity.ruc, max_pages
            )

        for page in range(max_pages):
            if page == 0 and first_soup is not None:
                soup = first_soup
            else:
                _, soup = client.fetch_list_page(page)
            rows = client.parse_rows(soup)
            logger.info(
                "[%s] página %s: %s procesos", entity.ruc, page + 1, len(rows)
            )
            if not rows and page > 0:
                break

            for row in rows:
                if not row.nid_proceso or row.nid_proceso in seen_nids:
                    continue
                seen_nids.add(row.nid_proceso)
                list_hash = row_snapshot_hash(row)
                proc = (
                    self.session.query(Process)
                    .filter(
                        Process.source == "seace",
                        Process.entity_id == entity.id,
                        Process.source_ref == row.nid_proceso,
                    )
                    .one_or_none()
                )

                if proc is not None and proc.status in (
                    ProcessStatus.descartada,
                    ProcessStatus.archivada,
                ):
                    proc.last_seen_at = utcnow()
                    continue

                savepoint = self.session.begin_nested()
                try:
                    if proc is None:
                        if self._upsert_from_ficha(
                            entity, client, row, proc=None, options=options, list_page=page
                        ):
                            new_count += 1
                    elif proc.list_hash != list_hash:
                        self._upsert_from_ficha(
                            entity, client, row, proc=proc, options=options, list_page=page
                        )
                    elif self._needs_ficha_refresh(proc):
                        self._upsert_from_ficha(
                            entity, client, row, proc=proc, options=options, list_page=page
                        )
                    else:
                        proc.last_seen_at = utcnow()
                    savepoint.commit()
                except _DateFilterSkip:
                    savepoint.rollback()
                except Exception:
                    savepoint.rollback()
                    logger.exception(
                        "Error procesando fila nid=%s nomenclatura=%s entidad=%s",
                        row.nid_proceso,
                        row.nomenclatura,
                        entity.ruc,
                    )

        return new_count

    def _needs_ficha_refresh(self, proc: Process) -> bool:
        if proc.status not in _FICHA_REFRESH_STATUSES:
            return False
        if not proc.updated_at:
            return True
        threshold = utcnow() - timedelta(seconds=self.config.ficha_refresh_seconds)
        updated = proc.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return updated < threshold

    def _upsert_from_ficha(
        self,
        entity: Entity,
        client: SeaceClient,
        row: ProcessRow,
        proc: Process | None,
        *,
        options: ScanOptions,
        list_page: int = 0,
    ) -> bool:
        is_new = proc is None
        ficha_result = client.open_ficha(row)
        ficha = parse_ficha(ficha_result.html, ficha_result.ficha_id, row.nid_proceso)
        client.refresh_list_page_state(list_page)
        fechas = extract_cronograma_fechas(ficha.cronograma)
        fecha_presentacion = fechas.fecha_presentacion or row.fecha_publicacion

        if options.date_mode and not passes_date_filter(
            fecha_presentacion,
            fecha_publicacion=row.fecha_publicacion,
            mode=options.date_mode,
            since_date=options.since_date,
        ):
            raise _DateFilterSkip()

        if proc is None:
            proc = Process(
                entity_id=entity.id,
                anio=self.config.anio,
                source="seace",
                source_ref=row.nid_proceso,
                nid_proceso=row.nid_proceso,
                status=ProcessStatus.publicada,
                first_seen_at=utcnow(),
            )
            self.session.add(proc)
        elif not proc.source_ref:
            proc.source = proc.source or "seace"
            proc.source_ref = proc.nid_proceso

        proc.ficha_id = ficha.ficha_id
        proc.nid_convocatoria = row.nid_convocatoria
        proc.nid_sistema = row.nid_sistema
        proc.link_id = row.link_id
        proc.ntipo = row.ntipo
        proc.numero = row.numero
        proc.fecha_publicacion = row.fecha_publicacion or ficha.fecha_publicacion
        proc.nomenclatura = row.nomenclatura
        proc.reiniciado_desde = row.reiniciado_desde
        proc.objeto = row.objeto
        proc.descripcion = row.descripcion
        proc.cuantia = row.cuantia
        proc.moneda = row.moneda
        proc.version_seace = row.version_seace
        proc.fecha_consultas = fechas.fecha_consultas
        proc.fecha_presentacion = fechas.fecha_presentacion
        proc.cronograma_json = json.dumps(
            [asdict(c) for c in ficha.cronograma], ensure_ascii=False
        )
        proc.ficha_url = ficha_result.url
        proc.list_hash = row_snapshot_hash(row)
        proc.content_hash = ficha.content_hash()
        proc.last_seen_at = utcnow()
        proc.updated_at = datetime.now(timezone.utc)
        match = apply_auto_reject_rules(proc, entity, self.auto_reject_rules)
        if match is not None:
            logger.info(
                "Autorechazado %s por regla %s — %s",
                row.nid_proceso,
                match.id,
                row.nomenclatura,
            )
        self.session.flush()

        logger.info(
            "%s %s — %s",
            "Nuevo" if is_new else "Actualizado",
            row.nid_proceso,
            row.nomenclatura,
        )
        return is_new


class _DateFilterSkip(Exception):
    """Fila omitida por filtro de fecha en escaneo acotado."""
