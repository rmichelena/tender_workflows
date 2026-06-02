"""Tests del overlay de decisiones por tenant (paso 0.3b)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db.models import Base, Entity, Process, ProcessStatus, TenantFeedDecision
from .db.session import _backfill_tenant_feed_decisions
from .feed import (
    clear_feed_decision,
    record_autoreject_decision,
    record_exempt_decision,
)


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20123456789", nombre="E", activa=True)
    session.add(entity)
    session.flush()
    return engine, session, entity


def _proc(entity, *, ref, status, reason=None, exempt=False):
    return Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref=ref,
        nid_proceso=ref,
        nomenclatura=f"NOM-{ref}",
        status=status,
        auto_reject_reason=reason,
        auto_reject_exempt=exempt,
    )


def _decisions(session):
    return {
        d.feed_item_id: d
        for d in session.query(TenantFeedDecision).filter_by(tenant_id="default")
    }


def test_create_all_creates_overlay_table():
    engine, session, _ = _setup()
    assert "tenant_feed_decisions" in Base.metadata.tables
    # La tabla existe y se puede consultar (sin filas).
    assert session.query(TenantFeedDecision).count() == 0


def test_record_autoreject_then_exempt_supersedes_same_row():
    _, session, entity = _setup()
    proc = _proc(entity, ref="1", status=ProcessStatus.autorejected,
                 reason="regla_x: motivo")
    session.add(proc)
    session.flush()

    record_autoreject_decision(session, proc, rule_id="regla_x", reason="regla_x: motivo")
    session.flush()
    d = _decisions(session)[proc.id]
    assert d.decision == "autorejected"
    assert d.rule_id == "regla_x"
    assert d.reason == "regla_x: motivo"

    # exempt actualiza la MISMA fila (UniqueConstraint tenant+feed_item).
    record_exempt_decision(session, proc)
    session.flush()
    rows = session.query(TenantFeedDecision).filter_by(feed_item_id=proc.id).all()
    assert len(rows) == 1
    assert rows[0].decision == "exempt"
    assert rows[0].rule_id is None


def test_record_flushes_when_process_id_missing():
    _, session, entity = _setup()
    proc = _proc(entity, ref="9", status=ProcessStatus.autorejected, reason="r: x")
    session.add(proc)  # sin flush explícito: id aún None
    record_autoreject_decision(session, proc, rule_id="r", reason="r: x")
    session.flush()
    assert proc.id is not None
    assert _decisions(session)[proc.id].decision == "autorejected"


def test_clear_feed_decision_removes_row():
    _, session, entity = _setup()
    proc = _proc(entity, ref="2", status=ProcessStatus.autorejected, reason="r: y")
    session.add(proc)
    session.flush()
    record_autoreject_decision(session, proc, rule_id="r", reason="r: y")
    session.flush()
    assert proc.id in _decisions(session)

    clear_feed_decision(session, proc)
    session.flush()
    assert proc.id not in _decisions(session)


def test_backfill_from_legacy_process_fields_is_idempotent():
    engine, session, entity = _setup()
    rejected = _proc(entity, ref="10", status=ProcessStatus.autorejected,
                     reason="servicio_limpieza: fuera de foco")
    exempt = _proc(entity, ref="11", status=ProcessStatus.publicada, exempt=True)
    normal = _proc(entity, ref="12", status=ProcessStatus.publicada)
    session.add_all([rejected, exempt, normal])
    session.commit()

    _backfill_tenant_feed_decisions(engine)
    _backfill_tenant_feed_decisions(engine)  # idempotente

    decisions = _decisions(session)
    assert decisions[rejected.id].decision == "autorejected"
    assert decisions[rejected.id].rule_id == "servicio_limpieza"
    assert decisions[rejected.id].reason == "servicio_limpieza: fuera de foco"
    assert decisions[exempt.id].decision == "exempt"
    assert normal.id not in decisions
    # Idempotencia: una sola fila por proceso.
    assert session.query(TenantFeedDecision).count() == 2
