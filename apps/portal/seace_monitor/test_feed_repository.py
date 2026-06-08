"""Tests del seam del feed (paso 0.3a del split feed/pipeline)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from datetime import datetime, timezone

from .db.models import Base, Entity, Process, ProcessStatus
from .feed import FeedRepository, record_autoreject_decision, record_exempt_decision


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _entity(session, ruc="20123456789"):
    entity = Entity(ruc=ruc, nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    return entity


def test_find_by_ref_matches_source_identity():
    session = _session()
    entity = _entity(session)
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="100",
        nid_proceso="100",
        nomenclatura="LP-1-2026",
    )
    session.add(proc)
    session.commit()

    repo = FeedRepository(session)
    assert repo.find_by_ref("seace", entity.id, "100") is proc
    # Identidad incluye la fuente: misma ref, otra fuente → no matchea.
    assert repo.find_by_ref("adp_portal", entity.id, "100") is None
    assert repo.find_by_ref("seace", entity.id, "999") is None


def test_query_by_status_filters_status_and_optional_source():
    session = _session()
    entity = _entity(session)
    seace_pub = Process(
        entity_id=entity.id, anio=2026, source="seace", source_ref="1",
        nid_proceso="1", nomenclatura="LP-1", status=ProcessStatus.publicada,
    )
    adp_pub = Process(
        entity_id=entity.id, anio=2026, source="adp_portal", source_ref="A1",
        nid_proceso=None, nomenclatura="ADP-1", status=ProcessStatus.publicada,
    )
    descartada = Process(
        entity_id=entity.id, anio=2026, source="seace", source_ref="2",
        nid_proceso="2", nomenclatura="LP-2", status=ProcessStatus.descartada,
    )
    session.add_all([seace_pub, adp_pub, descartada])
    session.commit()

    repo = FeedRepository(session)
    publicadas = repo.query_by_status([ProcessStatus.publicada]).all()
    assert set(publicadas) == {seace_pub, adp_pub}

    seace_only = repo.query_by_status(
        [ProcessStatus.publicada], source="seace"
    ).all()
    assert seace_only == [seace_pub]


def _proc(session, entity, *, ref, status, exempt=False):
    proc = Process(
        entity_id=entity.id, anio=2026, source="seace", source_ref=ref,
        nid_proceso=ref, nomenclatura=f"LP-{ref}", status=status,
        auto_reject_exempt=exempt,
    )
    session.add(proc)
    session.flush()
    return proc


def test_overlay_readers_return_decisions_per_tenant():
    session = _session()
    entity = _entity(session)
    rej = _proc(session, entity, ref="1", status=ProcessStatus.autorejected)
    exe = _proc(session, entity, ref="2", status=ProcessStatus.publicada, exempt=True)
    plain = _proc(session, entity, ref="3", status=ProcessStatus.publicada)
    record_autoreject_decision(session, rej, rule_id="limpieza", reason="limpieza: x")
    record_exempt_decision(session, exe)
    session.commit()

    repo = FeedRepository(session)
    assert repo.autorejected_feed_ids() == {rej.id}
    assert repo.exempt_feed_ids() == {exe.id}
    assert repo.decisions_for_tenant() == {
        rej.id: "autorejected",
        exe.id: "exempt",
    }
    assert plain.id not in repo.decisions_for_tenant()
    # Otro tenant no ve las decisiones del default.
    assert repo.autorejected_feed_ids(tenant_id="otro") == set()


def test_overlay_readers_match_legacy_status_fields():
    # Paridad 0.3c-1: con dual-write activo, el overlay refleja exactamente los campos
    # legacy de `Process` (autorejected ≡ status; exempt ≡ auto_reject_exempt).
    session = _session()
    entity = _entity(session)
    procs = [
        _proc(session, entity, ref="10", status=ProcessStatus.autorejected),
        _proc(session, entity, ref="11", status=ProcessStatus.autorejected),
        _proc(session, entity, ref="12", status=ProcessStatus.publicada, exempt=True),
        _proc(session, entity, ref="13", status=ProcessStatus.descargada, exempt=True),
        _proc(session, entity, ref="14", status=ProcessStatus.publicada),
    ]
    for p in procs:
        if p.status == ProcessStatus.autorejected:
            record_autoreject_decision(session, p, rule_id="r", reason="r: x")
        elif p.auto_reject_exempt:
            record_exempt_decision(session, p)
    session.commit()

    repo = FeedRepository(session)
    legacy_rejected = {
        p.id for p in procs if p.status == ProcessStatus.autorejected
    }
    legacy_exempt = {p.id for p in procs if p.auto_reject_exempt}
    assert repo.autorejected_feed_ids() == legacy_rejected
    assert repo.exempt_feed_ids() == legacy_exempt


def test_effective_autorejected_overlay_wins_over_status():
    # Régimen post-0.3c-3: el scanner deja el item en status=publicada y registra la
    # decisión solo en el overlay. El predicado efectivo debe detectarlo igual.
    session = _session()
    entity = _entity(session)
    flipped = _proc(session, entity, ref="1", status=ProcessStatus.publicada)
    record_autoreject_decision(session, flipped, rule_id="r", reason="r: x")
    session.commit()

    repo = FeedRepository(session)
    assert repo.effective_autorejected_ids() == {flipped.id}
    assert repo.is_effectively_autorejected(flipped) is True


def test_effective_autorejected_legacy_fallback_without_overlay():
    # Defensivo: item con status=autorejected legacy y sin fila en el overlay (p. ej.
    # previo al backfill) sigue contando como autorejected.
    session = _session()
    entity = _entity(session)
    legacy = _proc(session, entity, ref="2", status=ProcessStatus.autorejected)
    session.commit()

    repo = FeedRepository(session)
    assert repo.effective_autorejected_ids() == {legacy.id}
    assert repo.is_effectively_autorejected(legacy) is True


def test_effective_autorejected_exempt_overlay_supersedes_legacy_status():
    # Si el overlay dice 'exempt', manda sobre un status=autorejected legacy inconsistente.
    session = _session()
    entity = _entity(session)
    proc = _proc(session, entity, ref="3", status=ProcessStatus.autorejected)
    record_exempt_decision(session, proc)
    session.commit()

    repo = FeedRepository(session)
    assert repo.effective_autorejected_ids() == set()
    assert repo.is_effectively_autorejected(proc) is False
    assert repo.decision_for(proc) == "exempt"


def test_feed_regime_filters_promotion_latch():
    session = _session()
    entity = _entity(session)
    feed_pure = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="1",
        nid_proceso="1",
        nomenclatura="LP-1",
        status=ProcessStatus.publicada,
    )
    promoted = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="2",
        nid_proceso="2",
        nomenclatura="LP-2",
        status=ProcessStatus.publicada,
        promoted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    pipeline = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="3",
        nid_proceso="3",
        nomenclatura="LP-3",
        status=ProcessStatus.descargada,
        promoted_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    session.add_all([feed_pure, promoted, pipeline])
    session.commit()

    repo = FeedRepository(session)
    assert repo.is_feed_pure(feed_pure) is True
    assert repo.is_promoted(promoted) is True
    assert repo.promoted_ids() == {promoted.id, pipeline.id}

    pure_pub = repo.feed_pure_publicada_for_entity("seace", entity.id)
    assert pure_pub == [feed_pure]

    promo_pub = repo.promoted_publicada_for_entity("seace", entity.id)
    assert promo_pub == [promoted]

    all_pub = repo.by_status_for_entity(
        "seace", entity.id, (ProcessStatus.publicada,), regime="all"
    )
    assert set(all_pub) == {feed_pure, promoted}

    promoted_only = repo.query_by_status(
        [ProcessStatus.publicada, ProcessStatus.descargada], regime="promoted"
    ).all()
    assert set(promoted_only) == {promoted, pipeline}
