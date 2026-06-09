"""Tests del overlay de decisiones por tenant (paso 0.3b)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db.models import Base, Entity, FeedItem, ProcessStatus, TenantFeedDecision
from .db.session import (
    _backfill_tenant_feed_decisions,
    _flip_autorejected_status_to_overlay,
    _purge_orphan_feed_decisions,
)
from .feed import (
    clear_all_feed_decisions,
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
    return FeedItem(
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


def test_clear_all_feed_decisions_removes_every_tenant():
    _, session, entity = _setup()
    proc = _proc(entity, ref="3", status=ProcessStatus.publicada)
    session.add(proc)
    session.flush()
    record_autoreject_decision(session, proc, rule_id="r", reason="r: y")
    session.add(
        TenantFeedDecision(tenant_id="otro", feed_item_id=proc.id, decision="exempt")
    )
    session.flush()
    assert session.query(TenantFeedDecision).filter_by(feed_item_id=proc.id).count() == 2

    clear_all_feed_decisions(session, proc)
    session.flush()
    assert session.query(TenantFeedDecision).filter_by(feed_item_id=proc.id).count() == 0


def test_record_autoreject_does_not_overwrite_exempt():
    # Race restaurar↔scanner: una exención explícita del usuario no debe ser pisada por un
    # autoreject automático posterior.
    _, session, entity = _setup()
    proc = _proc(entity, ref="50", status=ProcessStatus.publicada, exempt=True)
    session.add(proc)
    session.flush()
    record_exempt_decision(session, proc)
    session.flush()
    assert _decisions(session)[proc.id].decision == "exempt"

    record_autoreject_decision(session, proc, rule_id="r", reason="r: x")
    session.flush()
    # Sigue exento (exempt supersede a autorejected).
    assert _decisions(session)[proc.id].decision == "exempt"


def test_record_exempt_overwrites_autorejected():
    # En sentido inverso sí: restaurar (exempt) supersede a un autorejected previo.
    _, session, entity = _setup()
    proc = _proc(entity, ref="51", status=ProcessStatus.autorejected, reason="r: x")
    session.add(proc)
    session.flush()
    record_autoreject_decision(session, proc, rule_id="r", reason="r: x")
    session.flush()
    record_exempt_decision(session, proc)
    session.flush()
    assert _decisions(session)[proc.id].decision == "exempt"


def test_flip_moves_autorejected_with_exempt_overlay_to_publicada():
    # Medium: un item status=autorejected legacy con overlay=exempt quedaba atrapado
    # (invisible en UI). El flip debe devolverlo a publicada (la decisión vive en overlay).
    engine, session, entity = _setup()
    proc = _proc(entity, ref="60", status=ProcessStatus.autorejected)
    session.add(proc)
    session.flush()
    record_exempt_decision(session, proc)
    session.commit()

    flipped = _flip_autorejected_status_to_overlay(engine)
    session.expire_all()

    assert flipped == 1
    assert session.get(FeedItem, proc.id).status == ProcessStatus.publicada
    assert _decisions(session)[proc.id].decision == "exempt"


def test_flip_autorejected_status_moves_to_publicada_idempotently():
    # 0.3c-3: el flip one-shot devuelve a `publicada` los items con status=autorejected
    # legacy que ya tienen su decisión en el overlay; idempotente y no toca otros estados.
    engine, session, entity = _setup()
    rejected = _proc(entity, ref="30", status=ProcessStatus.autorejected,
                     reason="r: x")
    descartada = _proc(entity, ref="31", status=ProcessStatus.descartada)
    session.add_all([rejected, descartada])
    session.commit()
    _backfill_tenant_feed_decisions(engine)  # crea la decisión overlay del rejected

    flipped = _flip_autorejected_status_to_overlay(engine)
    flipped_again = _flip_autorejected_status_to_overlay(engine)  # idempotente
    session.expire_all()

    assert flipped == 1
    assert flipped_again == 0
    assert session.get(FeedItem, rejected.id).status == ProcessStatus.publicada
    assert session.get(FeedItem, descartada.id).status == ProcessStatus.descartada
    # La decisión del overlay se conserva (la fuente de verdad del autoreject).
    assert _decisions(session)[rejected.id].decision == "autorejected"


def test_flip_skips_autorejected_without_overlay_decision():
    # Guard: si por alguna razón no hay decisión en el overlay, NO se flipea (evita
    # perder la marca de autoreject sin respaldo).
    engine, session, entity = _setup()
    orphan = _proc(entity, ref="40", status=ProcessStatus.autorejected)
    session.add(orphan)
    session.commit()

    flipped = _flip_autorejected_status_to_overlay(engine)
    session.expire_all()

    assert flipped == 0
    assert session.get(FeedItem, orphan.id).status == ProcessStatus.autorejected


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


def test_purge_orphan_feed_decisions_removes_only_dangling_rows():
    engine, session, entity = _setup()
    alive = _proc(entity, ref="20", status=ProcessStatus.autorejected, reason="r: y")
    session.add(alive)
    session.flush()
    record_autoreject_decision(session, alive, rule_id="r", reason="r: y")
    session.commit()

    # Decisión huérfana: feed_item_id que no existe en `processes`.
    session.add(
        TenantFeedDecision(
            tenant_id="default",
            feed_item_id=99999,
            decision="exempt",
        )
    )
    session.commit()
    assert session.query(TenantFeedDecision).count() == 2

    removed = _purge_orphan_feed_decisions(engine)
    removed_again = _purge_orphan_feed_decisions(engine)  # idempotente
    session.expire_all()

    assert removed == 1
    assert removed_again == 0
    remaining = {d.feed_item_id for d in session.query(TenantFeedDecision)}
    assert remaining == {alive.id}
