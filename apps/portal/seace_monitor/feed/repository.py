"""Acceso al feed de descubrimiento (seam del split feed/pipeline, paso 0.3a).

El feed es el firehose ruidoso de items descubiertos por los adapters (SEACE, ADP, …).
Conceptualmente es `FeedItem` (`docs/INGEST_CONTRACT.md` §3), pero hoy se materializa
sobre la tabla `processes`/`Process`. Centralizar el acceso aquí permite:

- que scanners y list views no enramen consultas crudas al ORM, y
- migrar a una tabla `feed_items` separada (paso 0.3e) sin tocar a los clientes.

Este paso es **behavior-preserving**: las consultas son las mismas que estaban inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ..db.models import Process, ProcessStatus, TenantFeedDecision
from .decisions import DECISION_AUTOREJECTED, DECISION_EXEMPT, DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session


class FeedRepository:
    """Acceso al feed compartido, materializado sobre `processes` por ahora."""

    def __init__(self, session: "Session") -> None:
        self.session = session

    def find_by_ref(
        self, source: str, entity_id: int, source_ref: str | None
    ) -> Process | None:
        """Item del feed por identidad de fuente ``(source, entity_id, source_ref)``.

        Es el dedupe del descubrimiento: el scanner lo usa para decidir alta vs update.
        """
        return (
            self.session.query(Process)
            .filter(
                Process.source == source,
                Process.entity_id == entity_id,
                Process.source_ref == source_ref,
            )
            .one_or_none()
        )

    def query_by_status(
        self, statuses: Iterable[ProcessStatus], *, source: str | None = None
    ) -> "Query":
        """Query base de items del feed en los estados dados (opcional: por fuente).

        Devuelve un `Query` para que el caller añada `options()`/orden según su vista.
        """
        query = self.session.query(Process).filter(
            Process.status.in_(list(statuses))
        )
        if source is not None:
            query = query.filter(Process.source == source)
        return query

    def by_status_for_entity(
        self,
        source: str,
        entity_id: int,
        statuses: Iterable[ProcessStatus],
    ) -> list[Process]:
        """Items de una entidad y fuente en los estados dados.

        Sirve para reconciliar re-publicaciones por UID de negocio (nomenclatura) sin
        depender del `source_ref`/nid, que SEACE reasigna al re-publicar un proceso.
        """
        return (
            self.session.query(Process)
            .filter(
                Process.source == source,
                Process.entity_id == entity_id,
                Process.status.in_(list(statuses)),
            )
            .all()
        )

    def claimed_for_entity(
        self,
        source: str,
        entity_id: int,
        statuses: Iterable[ProcessStatus],
    ) -> list[Process]:
        """Items "reclamados" (descargados/analizados/…) de una entidad y fuente."""
        return self.by_status_for_entity(source, entity_id, statuses)

    # --- Overlay de decisiones por tenant (split feed/pipeline, paso 0.3c) ---
    # Estos lectores derivan la decisión de autoreject/exempt desde el overlay
    # `TenantFeedDecision` en vez de los campos `Process.status`/`auto_reject_exempt`.
    # Mientras dure la doble escritura (0.3b) coinciden con el feed; en 0.3c los reads
    # pasan a apoyarse en el overlay para que el feed sea agnóstico al tenant.

    def decisions_for_tenant(
        self, tenant_id: str = DEFAULT_TENANT_ID
    ) -> dict[int, str]:
        """Mapa ``feed_item_id -> decisión`` (``autorejected``|``exempt``) del tenant."""
        return {
            feed_item_id: decision
            for feed_item_id, decision in (
                self.session.query(
                    TenantFeedDecision.feed_item_id, TenantFeedDecision.decision
                )
                .filter(TenantFeedDecision.tenant_id == tenant_id)
                .all()
            )
        }

    def autorejected_feed_ids(
        self, tenant_id: str = DEFAULT_TENANT_ID
    ) -> set[int]:
        """ids de items con decisión ``autorejected`` para el tenant."""
        return {
            row[0]
            for row in self.session.query(TenantFeedDecision.feed_item_id)
            .filter(
                TenantFeedDecision.tenant_id == tenant_id,
                TenantFeedDecision.decision == DECISION_AUTOREJECTED,
            )
            .all()
        }

    def exempt_feed_ids(self, tenant_id: str = DEFAULT_TENANT_ID) -> set[int]:
        """ids de items que el tenant eximió del autoreject (``exempt``)."""
        return {
            row[0]
            for row in self.session.query(TenantFeedDecision.feed_item_id)
            .filter(
                TenantFeedDecision.tenant_id == tenant_id,
                TenantFeedDecision.decision == DECISION_EXEMPT,
            )
            .all()
        }

    # --- Predicados "bi-régimen" (paso 0.3c-2) ---
    # Resuelven la decisión efectiva combinando overlay + campos legacy de `Process`,
    # con la regla "el overlay manda, fallback al legacy". Así las lecturas dan el mismo
    # resultado mientras el scanner aún muta `status` (0.3c-2, doble escritura) y siguen
    # correctas cuando deje de mutarlo (0.3c-3): el item autorejected quedará en
    # `status=publicada` pero con decisión `autorejected` en el overlay.

    def effective_autorejected_ids(
        self, tenant_id: str = DEFAULT_TENANT_ID
    ) -> set[int]:
        """ids efectivamente autorejected (overlay manda; fallback a `status` legacy)."""
        decisions = self.decisions_for_tenant(tenant_id)
        overlay = {
            fid for fid, d in decisions.items() if d == DECISION_AUTOREJECTED
        }
        legacy = {
            row[0]
            for row in self.session.query(Process.id)
            .filter(Process.status == ProcessStatus.autorejected)
            .all()
            if row[0] not in decisions  # el overlay tiene prioridad si opinó
        }
        return overlay | legacy

    def autoreject_reasons(
        self, tenant_id: str = DEFAULT_TENANT_ID
    ) -> dict[int, str | None]:
        """Mapa ``feed_item_id -> motivo`` de las decisiones ``autorejected`` del tenant."""
        return {
            feed_item_id: reason
            for feed_item_id, reason in (
                self.session.query(
                    TenantFeedDecision.feed_item_id, TenantFeedDecision.reason
                )
                .filter(
                    TenantFeedDecision.tenant_id == tenant_id,
                    TenantFeedDecision.decision == DECISION_AUTOREJECTED,
                )
                .all()
            )
        }

    def decision_for(
        self, process: Process, tenant_id: str = DEFAULT_TENANT_ID
    ) -> str | None:
        """Decisión del overlay para un item (``autorejected``|``exempt``|``None``)."""
        if process.id is None:
            return None
        row = (
            self.session.query(TenantFeedDecision.decision)
            .filter_by(tenant_id=tenant_id, feed_item_id=process.id)
            .one_or_none()
        )
        return row[0] if row is not None else None

    def is_effectively_autorejected(
        self, process: Process, tenant_id: str = DEFAULT_TENANT_ID
    ) -> bool:
        """¿El item está autorejected? (overlay manda; fallback a `status` legacy)."""
        decision = self.decision_for(process, tenant_id)
        if decision is not None:
            return decision == DECISION_AUTOREJECTED
        return process.status == ProcessStatus.autorejected
