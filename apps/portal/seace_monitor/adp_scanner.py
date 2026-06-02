"""Escáner del portal ADP: detecta procesos nuevos y cambios.

Funciona de forma análoga a :class:`MultiEntityScanner` (SEACE) pero
simplificado — el portal ADP devuelve todo en un solo GET por categoría.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .adp_client import ALL_WORK_IDS, WORK_CATEGORIES, AdpClient
from .adp_parser import AdpProcess, parse_adp_html
from .auto_reject import AutoRejectRule, apply_auto_reject_rules, load_auto_reject_rules
from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus, utcnow
from .feed import FeedRepository, record_autoreject_decision

logger = logging.getLogger(__name__)

ADP_PORTAL_SOURCE = "adp_portal"
ADP_ENTITY_RUC = "ADP-PORTAL"
ADP_ENTITY_NAME = "Aeropuertos del Perú"


def _ensure_adp_entity(session: Session) -> Entity:
    """Crea la entidad ADP si no existe y la retorna."""
    entity = session.query(Entity).filter(Entity.ruc == ADP_ENTITY_RUC).one_or_none()
    if entity:
        return entity
    entity = Entity(
        ruc=ADP_ENTITY_RUC,
        nombre=ADP_ENTITY_NAME,
        activa=True,
    )
    session.add(entity)
    session.flush()
    logger.info("Entidad ADP creada (ruc=%s)", ADP_ENTITY_RUC)
    return entity


def _adp_doc_to_dict(doc) -> dict:
    """Convierte un :class:`AdpDocument` a diccionario para ``documentos_json``."""
    return {
        "name_file": doc.name_file,
        "new_name": doc.new_name,
        "title": doc.title,
        "vigencia_desde": doc.vigencia_desde,
        "vigencia_hasta": doc.vigencia_hasta,
        "download_url": doc.download_url,
        "archivo": "",
        "uuid": doc.name_file,  # usamos name_file como identificador estable
    }


def _adp_process_to_cronograma(process: AdpProcess) -> list[dict]:
    """Genera un cronograma simplificado a partir de las fechas de documentos."""
    entries: list[dict] = []
    for doc in process.documents:
        if doc.vigencia_desde or doc.vigencia_hasta:
            entries.append(
                {
                    "titulo": doc.title,
                    "fecha_desde": doc.vigencia_desde,
                    "fecha_hasta": doc.vigencia_hasta,
                }
            )
    return entries


class AdpScanner:
    """Escáner del portal de Aeropuertos del Perú."""

    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session
        self.feed = FeedRepository(session)
        self.auto_reject_rules: list[AutoRejectRule] = load_auto_reject_rules(config)
        self.client = AdpClient(http_proxy=config.http_proxy)

    def run_once(self) -> int:
        """Escanea las 4 categorías ADP y registra procesos nuevos.

        Returns:
            Número de procesos nuevos detectados.
        """
        entity = _ensure_adp_entity(self.session)
        new_count = 0

        try:
            for work_id in ALL_WORK_IDS:
                cat = WORK_CATEGORIES.get(work_id, str(work_id))
                try:
                    html = self.client.fetch_category_html(work_id)
                    processes = parse_adp_html(html, work_id)
                    logger.info(
                        "ADP work_id=%s (%s): %s procesos",
                        work_id,
                        cat,
                        len(processes),
                    )
                except Exception:
                    logger.exception("ADP: error fetching work_id=%s", work_id)
                    continue

                for adp_proc in processes:
                    savepoint = self.session.begin_nested()
                    try:
                        if self._upsert_process(entity, adp_proc):
                            new_count += 1
                        savepoint.commit()
                    except Exception:
                        savepoint.rollback()
                        logger.exception(
                            "ADP: error procesando %s", adp_proc.code
                        )
        finally:
            self.client.close()

        return new_count

    def _upsert_process(self, entity: Entity, adp_proc: AdpProcess) -> bool:
        """Crea o actualiza un proceso ADP en la BD.

        Returns:
            ``True`` si el proceso es nuevo.
        """
        content_hash = adp_proc.content_hash()
        proc = self.feed.find_by_ref(ADP_PORTAL_SOURCE, entity.id, adp_proc.code)

        is_new = proc is None

        if proc is not None and proc.status in (
            ProcessStatus.descartada,
            ProcessStatus.archivada,
        ):
            proc.last_seen_at = utcnow()
            return False

        if proc is not None and proc.content_hash == content_hash:
            proc.last_seen_at = utcnow()
            return False

        docs_json = json.dumps(
            [_adp_doc_to_dict(d) for d in adp_proc.documents],
            ensure_ascii=False,
        )
        cron_json = json.dumps(
            _adp_process_to_cronograma(adp_proc),
            ensure_ascii=False,
        )

        # Extraer año del código (e.g. "LPN-003-2026-ADP" → 2026)
        anio = self._extract_anio(adp_proc.code)

        if proc is None:
            proc = Process(
                entity_id=entity.id,
                anio=anio,
                source=ADP_PORTAL_SOURCE,
                source_ref=adp_proc.code,
                nomenclatura=adp_proc.code,
                status=ProcessStatus.publicada,
                first_seen_at=utcnow(),
            )
            self.session.add(proc)

        proc.objeto = WORK_CATEGORIES.get(adp_proc.work_id, "bienes").capitalize()
        proc.descripcion = adp_proc.description
        proc.documentos_json = docs_json
        proc.cronograma_json = cron_json
        proc.content_hash = content_hash
        proc.last_seen_at = utcnow()
        proc.updated_at = datetime.now(timezone.utc)
        if anio:
            proc.anio = anio

        # Auto-reject (solo si está en publicada y es nuevo)
        match = apply_auto_reject_rules(proc, entity, self.auto_reject_rules) if is_new else None
        self.session.flush()
        if match is not None:
            record_autoreject_decision(
                self.session, proc, rule_id=match.id, reason=proc.auto_reject_reason
            )
            logger.info(
                "ADP autorechazado %s por regla %s",
                adp_proc.code,
                match.id,
            )
        action = "Nuevo" if is_new else "Actualizado"
        logger.info("ADP %s: %s", action, adp_proc.code)
        return is_new

    @staticmethod
    def _extract_anio(code: str) -> int:
        """Extrae el año del código de proceso (e.g. 'LPN-003-2026-ADP' → 2026)."""
        m = re.search(r"(20\d{2})", code)
        if m:
            return int(m.group(1))
        return datetime.now().year
