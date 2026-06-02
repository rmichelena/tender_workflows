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
from .feed import FeedRepository, record_autoreject_decision
from .parser import extract_cronograma_fechas, parse_ficha, row_snapshot_hash
from .scan_options import ScanOptions, passes_date_filter
from .seace_search import normalize_nomenclatura

logger = logging.getLogger(__name__)

_FICHA_REFRESH_STATUSES = frozenset(
    {
        ProcessStatus.publicada,
        ProcessStatus.descargada,
    }
)

# Estados "reclamados": ya seguimos/trabajamos el proceso (descarga/análisis/portafolio).
# Cuando SEACE re-publica un proceso interrumpido reasigna el nid pero conserva la
# nomenclatura; el scanner contrasta por nomenclatura (UID de negocio) contra estas
# filas para fusionar el scan en un update en vez de crear un duplicado `publicada`.
_REPUBLICATION_CLAIMED_STATUSES = frozenset(
    {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
)


# Orden de avance entre estados reclamados (mayor = más trabajo invertido). Sirve para
# desempatar si dos procesos reclamados comparten nomenclatura (datos legacy).
_CLAIMED_STATUS_RANK = {
    ProcessStatus.descargando: 0,
    ProcessStatus.descargada: 1,
    ProcessStatus.analizada: 2,
    ProcessStatus.portafolio: 3,
}


def _nid_int(value: str | None) -> int:
    """nid SEACE como entero (crece con el tiempo); -1 si no es numérico."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return -1


def _nid_advances(current: str | None, new: str | None) -> bool:
    """¿`new` es un nid más reciente que `current`? (nunca regresar identidad)."""
    new_n = _nid_int(new)
    if new_n < 0:
        return False
    cur_n = _nid_int(current)
    return cur_n < 0 or new_n > cur_n


def is_removable_publicada_duplicate(proc: Process) -> bool:
    """¿`proc` es un duplicado `publicada` sin datos propios (seguro de borrar)?"""
    return (
        proc.status == ProcessStatus.publicada
        and not proc.data_dir
        and proc.analysis is None
    )


def build_claimed_nomenclatura_map(processes: list[Process]) -> dict[str, Process]:
    """Mapa nomenclatura-normalizada → proceso reclamado preferido.

    Ante varios reclamados con la misma nomenclatura (datos legacy / fusión incompleta)
    elige el más avanzado y, a igualdad, el de nid más reciente, y lo registra en logs;
    así la reconciliación no depende del orden no determinístico de la consulta.
    """
    mapping: dict[str, Process] = {}
    for proc in processes:
        key = normalize_nomenclatura(proc.nomenclatura)
        if not key:
            continue
        current = mapping.get(key)
        if current is None:
            mapping[key] = proc
            continue
        logger.warning(
            "Nomenclatura duplicada entre reclamados: %s (id=%s estado=%s vs id=%s estado=%s)",
            key,
            current.id,
            current.status.value if current.status else "?",
            proc.id,
            proc.status.value if proc.status else "?",
        )
        if _claimed_is_preferred(proc, current):
            mapping[key] = proc
    return mapping


def _claimed_is_preferred(candidate: Process, current: Process) -> bool:
    """¿`candidate` representa mejor al item que `current`? (más avanzado, luego nid)."""
    cand_rank = _CLAIMED_STATUS_RANK.get(candidate.status, -1)
    cur_rank = _CLAIMED_STATUS_RANK.get(current.status, -1)
    if cand_rank != cur_rank:
        return cand_rank > cur_rank
    return _nid_int(candidate.source_ref) > _nid_int(current.source_ref)


def adopt_republication(
    session, claimed: Process, existing: Process | None, row
) -> None:
    """Fusiona una re-publicación en el item ya reclamado `claimed`.

    `existing` es la fila hallada por `source_ref` (nuevo nid) si la hubiera: si es un
    duplicado `publicada` sin datos, se elimina para liberar la identidad. Si la
    identidad queda libre (o no existía) y el nid **avanza** (no regresar a un nid ya
    superado en el mismo scan), se adopta el nuevo nid en `claimed`. El refresco de
    contenido (cronograma/documentos) lo sigue gobernando el watchlist, que resuelve por
    nomenclatura y preserva docs/changelog.
    """
    if existing is not None and is_removable_publicada_duplicate(existing):
        session.delete(existing)
        session.flush()
        existing = None
    if existing is None and _nid_advances(claimed.source_ref, row.nid_proceso):
        logger.info(
            "Re-publicación %s: adopto nid %s → %s en proceso id=%s (%s)",
            row.nomenclatura,
            claimed.source_ref,
            row.nid_proceso,
            claimed.id,
            claimed.status.value if claimed.status else "?",
        )
        claimed.source_ref = row.nid_proceso
        claimed.nid_proceso = row.nid_proceso
        if row.reiniciado_desde:
            claimed.reiniciado_desde = row.reiniciado_desde
        claimed.list_hash = row_snapshot_hash(row)
    claimed.last_seen_at = utcnow()


class MultiEntityScanner:
    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session
        self.feed = FeedRepository(session)
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
        claimed_by_nomenclatura = self._claimed_nomenclatura_map(entity)

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
                proc = self.feed.find_by_ref("seace", entity.id, row.nid_proceso)

                claimed = claimed_by_nomenclatura.get(
                    normalize_nomenclatura(row.nomenclatura)
                )
                if claimed is not None and claimed is not proc:
                    # Re-publicación de un proceso ya reclamado: fusionar, no duplicar.
                    savepoint = self.session.begin_nested()
                    try:
                        adopt_republication(self.session, claimed, proc, row)
                        savepoint.commit()
                    except Exception:
                        savepoint.rollback()
                        logger.exception(
                            "Error fusionando re-publicación nid=%s nomenclatura=%s",
                            row.nid_proceso,
                            row.nomenclatura,
                        )
                    continue

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

    def _claimed_nomenclatura_map(self, entity: Entity) -> dict[str, Process]:
        """Mapa nomenclatura-normalizada → proceso reclamado de la entidad.

        Permite contrastar cada fila escaneada por UID de negocio (nomenclatura) contra
        los procesos ya descargados/analizados, en vez de por el nid que SEACE reasigna.
        """
        claimed = self.feed.claimed_for_entity(
            "seace", entity.id, _REPUBLICATION_CLAIMED_STATUSES
        )
        return build_claimed_nomenclatura_map(claimed)

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
        self.session.flush()
        if match is not None:
            record_autoreject_decision(
                self.session, proc, rule_id=match.id, reason=proc.auto_reject_reason
            )
            logger.info(
                "Autorechazado %s por regla %s — %s",
                row.nid_proceso,
                match.id,
                row.nomenclatura,
            )

        logger.info(
            "%s %s — %s",
            "Nuevo" if is_new else "Actualizado",
            row.nid_proceso,
            row.nomenclatura,
        )
        return is_new


class _DateFilterSkip(Exception):
    """Fila omitida por filtro de fecha en escaneo acotado."""
