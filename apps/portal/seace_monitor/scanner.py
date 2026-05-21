"""Escáner multi-entidad: listado + ficha (sin descargar documentos)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .client import ProcessRow, SeaceClient
from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus, utcnow
from .entities import EntityRow, load_entities_csv
from .parser import extract_cronograma_fechas, parse_ficha, row_snapshot_hash

logger = logging.getLogger(__name__)

_FICHA_REFRESH_STATUSES = frozenset(
    {
        ProcessStatus.publicada,
        ProcessStatus.descargando,
        ProcessStatus.descargada,
    }
)


def sync_entities(session: Session, rows: list[EntityRow]) -> dict[str, Entity]:
    by_ruc: dict[str, Entity] = {}
    for row in rows:
        ent = session.query(Entity).filter(Entity.ruc == row.ruc).one_or_none()
        if ent is None:
            ent = Entity(ruc=row.ruc, nombre=row.nombre)
            session.add(ent)
            session.flush()
        else:
            ent.nombre = row.nombre
        by_ruc[row.ruc] = ent
    session.commit()
    return by_ruc


class MultiEntityScanner:
    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session

    def run_once(self) -> int:
        entity_rows = load_entities_csv(self.config.entities_csv)
        entities = sync_entities(self.session, entity_rows)
        new_count = 0

        for ruc, entity in entities.items():
            if not entity.activa:
                continue
            try:
                n = self._scan_entity(entity)
                new_count += n
                self.session.commit()
            except Exception:
                self.session.rollback()
                logger.exception("Error escaneando entidad %s (%s)", entity.nombre, ruc)

        return new_count

    def _scan_entity(self, entity: Entity) -> int:
        client = SeaceClient(
            ruc_entidad=entity.ruc,
            anio=self.config.anio,
            rows_per_page=self.config.rows_per_page,
            http_proxy=self.config.http_proxy,
        )
        new_count = 0
        seen_nids: set[str] = set()

        # Paginación SEACE (REVIEW M5 — omitido a propósito):
        # Solo escaneamos config.max_pages (default 1 × rows_per_page filas). No llamamos
        # SeaceClient.total_pages() ni avisamos si SEACE tiene más páginas: nuestras entidades
        # caben en una página. Si alguna supera ~15 procesos visibles, subir max_pages en
        # config.yaml (H1 ya preserva ViewState por página).
        for page in range(self.config.max_pages):
            _, soup = client.fetch_list_page(page)
            rows = client.parse_rows(soup)
            logger.info(
                "[%s] página %s: %s procesos", entity.ruc, page + 1, len(rows)
            )

            for row in rows:
                if not row.nid_proceso or row.nid_proceso in seen_nids:
                    continue
                seen_nids.add(row.nid_proceso)
                list_hash = row_snapshot_hash(row)
                proc = (
                    self.session.query(Process)
                    .filter(
                        Process.entity_id == entity.id,
                        Process.nid_proceso == row.nid_proceso,
                    )
                    .one_or_none()
                )

                if proc is not None and proc.status == ProcessStatus.descartada:
                    proc.last_seen_at = utcnow()
                    continue

                savepoint = self.session.begin_nested()
                try:
                    if proc is None:
                        if self._upsert_from_ficha(entity, client, row, proc=None):
                            new_count += 1
                    elif proc.list_hash != list_hash:
                        self._upsert_from_ficha(entity, client, row, proc=proc)
                    elif self._needs_ficha_refresh(proc):
                        self._upsert_from_ficha(entity, client, row, proc=proc)
                    else:
                        proc.last_seen_at = utcnow()
                    savepoint.commit()
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
    ) -> bool:
        is_new = proc is None
        ficha_result = client.open_ficha(row)
        ficha = parse_ficha(ficha_result.html, ficha_result.ficha_id, row.nid_proceso)
        fechas = extract_cronograma_fechas(ficha.cronograma)

        if proc is None:
            proc = Process(
                entity_id=entity.id,
                anio=self.config.anio,
                nid_proceso=row.nid_proceso,
                status=ProcessStatus.publicada,
                first_seen_at=utcnow(),
            )
            self.session.add(proc)

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
        proc.documentos_json = json.dumps(
            [asdict(d) for d in ficha.documentos], ensure_ascii=False
        )
        proc.ficha_url = ficha_result.url
        proc.list_hash = row_snapshot_hash(row)
        proc.content_hash = ficha.content_hash()
        proc.last_seen_at = utcnow()
        proc.updated_at = datetime.now(timezone.utc)
        self.session.flush()

        logger.info(
            "%s %s — %s",
            "Nuevo" if is_new else "Actualizado",
            row.nid_proceso,
            row.nomenclatura,
        )
        return is_new
