"""Tests para la migración 0.3e-1: PipelineItem model + backfill from processes."""

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect

from seace_monitor.db.models import (
    AnalysisResult,
    Base,
    Entity,
    PipelineItem,
    Process,
    ProcessStatus,
    InterestStatus,
    utcnow,
)
from seace_monitor.db.session import init_db


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url)
    Base.metadata.create_all(eng)
    return eng


def _seed_promoted_process(eng, **overrides):
    """Inserta un Process promoted + Entity asociada."""
    with eng.begin() as conn:
        conn.execute(
            conn.execute.__self__.execute.__func__  # just use text
            if False else None
        ) if False else None
    # Use ORM-style via session
    from sqlalchemy.orm import Session
    with Session(eng) as s:
        entity = Entity(ruc="12345678901", nombre="Test Entity", activa=True)
        s.add(entity)
        s.flush()
        defaults = dict(
            entity_id=entity.id,
            anio=2026,
            source="seace",
            source_ref="12345",
            nomenclatura="LP-01-2026",
            objeto="Test objeto",
            status=ProcessStatus.descargada,
            promoted_at=utcnow(),
            data_dir="/data/test",
        )
        defaults.update(overrides)
        proc = Process(**defaults)
        s.add(proc)
        s.flush()
        return proc.id, entity.id


class TestPipelineItemModel:
    """El modelo PipelineItem se crea correctamente."""

    def test_table_created_on_init(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(db_url)
        eng = create_engine(db_url)
        insp = inspect(eng)
        assert "pipeline_items" in insp.get_table_names()

    def test_pipeline_item_columns(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(db_url)
        eng = create_engine(db_url)
        insp = inspect(eng)
        cols = {col["name"] for col in insp.get_columns("pipeline_items")}
        # Essential columns
        assert "id" in cols
        assert "tenant_id" in cols
        assert "origin_feed_id" in cols
        assert "origin_source" in cols
        assert "origin_source_ref" in cols
        assert "entity_id" in cols
        assert "status" in cols
        assert "workflow_profile" in cols
        assert "interest_status" in cols
        assert "lifecycle_phase" in cols
        assert "promoted_at" in cols
        # Pipeline-only fields
        assert "data_dir" in cols
        assert "cronograma_json" in cols
        assert "documentos_json" in cols
        assert "watch_unread" in cols


class TestBackfillPipelineItems:
    """La migración copia promoted processes → pipeline_items."""

    def test_backfill_promoted(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)

        from sqlalchemy.orm import Session
        from seace_monitor.db.models import Entity, Process, ProcessStatus, utcnow

        with Session(eng) as s:
            entity = Entity(ruc="11111111111", nombre="E1", activa=True)
            s.add(entity)
            s.flush()

            # promoted process
            p = Process(
                entity_id=entity.id,
                anio=2026,
                source="seace",
                source_ref="S1",
                nomenclatura="LP-01-2026",
                status=ProcessStatus.descargada,
                promoted_at=utcnow(),
                data_dir="/data/p1",
            )
            s.add(p)

            # feed-pure process (should NOT be copied)
            p2 = Process(
                entity_id=entity.id,
                anio=2026,
                source="seace",
                source_ref="S2",
                nomenclatura="LP-02-2026",
                status=ProcessStatus.publicada,
                promoted_at=None,
            )
            s.add(p2)
            s.commit()
            promoted_id = p.id
            feed_pure_id = p2.id

        # Run init_db (which calls _backfill_pipeline_items)
        init_db(db_url)

        from sqlalchemy import text
        with eng.connect() as conn:
            # Should have 1 pipeline_item (the promoted one)
            pi_rows = conn.execute(text("SELECT origin_feed_id, tenant_id, data_dir FROM pipeline_items")).fetchall()
            assert len(pi_rows) == 1
            assert pi_rows[0][0] == promoted_id
            assert pi_rows[0][1] == "default"
            assert pi_rows[0][2] == "/data/p1"

    def test_backfill_idempotent(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)

        from sqlalchemy.orm import Session
        from seace_monitor.db.models import Entity, Process, ProcessStatus, utcnow

        with Session(eng) as s:
            entity = Entity(ruc="11111111111", nombre="E1", activa=True)
            s.add(entity)
            s.flush()
            p = Process(
                entity_id=entity.id,
                anio=2026,
                source="seace",
                source_ref="S1",
                nomenclatura="LP-01-2026",
                status=ProcessStatus.analizada,
                promoted_at=utcnow(),
            )
            s.add(p)
            s.commit()

        init_db(db_url)
        init_db(db_url)  # second run

        from sqlalchemy import text
        with eng.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM pipeline_items")).scalar()
            assert count == 1

    def test_backfill_analysis_pipeline_item_id(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)

        from sqlalchemy.orm import Session
        from seace_monitor.db.models import Entity, Process, ProcessStatus, AnalysisResult, utcnow

        with Session(eng) as s:
            entity = Entity(ruc="11111111111", nombre="E1", activa=True)
            s.add(entity)
            s.flush()
            p = Process(
                entity_id=entity.id,
                anio=2026,
                source="seace",
                source_ref="S1",
                nomenclatura="LP-01-2026",
                status=ProcessStatus.analizada,
                promoted_at=utcnow(),
            )
            s.add(p)
            s.flush()
            ar = AnalysisResult(process_id=p.id, status="done", alcance="test")
            s.add(ar)
            s.commit()
            process_id = p.id

        init_db(db_url)

        from sqlalchemy import text
        with eng.connect() as conn:
            pi_id = conn.execute(
                text("SELECT id FROM pipeline_items WHERE origin_feed_id = :pid"),
                {"pid": process_id},
            ).scalar()
            assert pi_id is not None
            ar_pi = conn.execute(
                text("SELECT pipeline_item_id FROM analysis_results WHERE process_id = :pid"),
                {"pid": process_id},
            ).scalar()
            assert ar_pi == pi_id

    def test_backfill_skips_feed_pure(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)

        from sqlalchemy.orm import Session
        from seace_monitor.db.models import Entity, Process, ProcessStatus

        with Session(eng) as s:
            entity = Entity(ruc="11111111111", nombre="E1", activa=True)
            s.add(entity)
            s.flush()
            p = Process(
                entity_id=entity.id,
                anio=2026,
                source="seace",
                source_ref="S1",
                nomenclatura="LP-01-2026",
                status=ProcessStatus.publicada,
                promoted_at=None,
            )
            s.add(p)
            s.commit()

        init_db(db_url)

        from sqlalchemy import text
        with eng.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM pipeline_items")).scalar()
            assert count == 0
