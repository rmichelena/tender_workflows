"""Overlay de decisiones por tenant sobre el feed (paso 0.3b).

Hoy las decisiones de autoreject viven en `FeedItem` (`status='autorejected'`,
`auto_reject_exempt`, `auto_reject_reason`). El split feed/pipeline las mueve a un
overlay por tenant (`TenantFeedDecision`) para que el feed sea compartido y agnóstico al
tenant. En 0.3b mantenemos **doble escritura**: seguimos mutando `FeedItem` (los reads no
cambian) y, en paralelo, registramos la decisión en el overlay; en 0.3c los reads pasarán
al overlay y el scanner dejará de mutar el feed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..db.models import TenantFeedDecision

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..db.models import FeedItem

DEFAULT_TENANT_ID = "default"
DECISION_AUTOREJECTED = "autorejected"
DECISION_EXEMPT = "exempt"


def _feed_item_id(session: "Session", process: "FeedItem") -> int | None:
    if process.id is None:
        session.flush()
    return process.id


def _upsert_decision(
    session: "Session",
    process: "FeedItem",
    decision: str,
    *,
    rule_id: str | None,
    reason: str | None,
    tenant_id: str,
) -> None:
    feed_item_id = _feed_item_id(session, process)
    if feed_item_id is None:
        return
    existing = (
        session.query(TenantFeedDecision)
        .filter_by(tenant_id=tenant_id, feed_item_id=feed_item_id)
        .one_or_none()
    )
    if existing is None:
        session.add(
            TenantFeedDecision(
                tenant_id=tenant_id,
                feed_item_id=feed_item_id,
                decision=decision,
                rule_id=rule_id,
                reason=reason,
            )
        )
        return
    # `exempt` (decisión explícita del usuario) supersede a `autorejected`: un autoreject
    # automático nunca debe pisar una exención. Evita la carrera restaurar↔scanner.
    if decision == DECISION_AUTOREJECTED and existing.decision == DECISION_EXEMPT:
        return
    existing.decision = decision
    existing.rule_id = rule_id
    existing.reason = reason


def record_autoreject_decision(
    session: "Session",
    process: "FeedItem",
    *,
    rule_id: str | None,
    reason: str | None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """Registra (overlay) que una regla auto-rechazó el item."""
    _upsert_decision(
        session,
        process,
        DECISION_AUTOREJECTED,
        rule_id=rule_id,
        reason=reason,
        tenant_id=tenant_id,
    )


def record_exempt_decision(
    session: "Session",
    process: "FeedItem",
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """Registra (overlay) que el tenant eximió el item del autoreject (restaurar)."""
    _upsert_decision(
        session,
        process,
        DECISION_EXEMPT,
        rule_id=None,
        reason=None,
        tenant_id=tenant_id,
    )


def clear_feed_decision(
    session: "Session",
    process: "FeedItem",
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """Elimina la decisión del overlay (p. ej. descarte manual de un autorejected)."""
    feed_item_id = process.id
    if feed_item_id is None:
        return
    session.query(TenantFeedDecision).filter_by(
        tenant_id=tenant_id, feed_item_id=feed_item_id
    ).delete()


def clear_all_feed_decisions(session: "Session", process: "FeedItem") -> None:
    """Borra las decisiones de **todos** los tenants sobre un feed item.

    El feed es compartido y sin FK al overlay: cuando el item se elimina (p. ej. el
    duplicado que fusiona `adopt_republication`), hay que purgar las decisiones de
    cualquier tenant, no solo del actual, para no dejar huérfanas.
    """
    feed_item_id = process.id
    if feed_item_id is None:
        return
    session.query(TenantFeedDecision).filter_by(feed_item_id=feed_item_id).delete()
