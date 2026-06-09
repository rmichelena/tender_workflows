"""Tests para dual-write 0.3e-2: Process promovido → PipelineItem auto-sync."""

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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
from seace_monitor.db.session import commit_session_with_retry


@pytest.fixture
def db(tmp_path):
    """DB en memoria con tablas creadas."""
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(eng)
    return eng


def _make_promoted(eng, **overrides):
    """Crea entity + process promoted."""
    with Session(eng) as s:
        entity = Entity(ruc="12345678901", nombre="Test", activa=True)
        s.add(entity)
        s.flush()
        defaults = dict(
            entity_id=entity.id,
            anio=2026,
            source="seace",
            source_ref="S001",
            nomenclatura="LP-01-2026",
            status=ProcessStatus.descargada,
            promoted_at=utcnow(),
            data_dir="/data/test",
        )
        defaults.update(overrides)
        proc = Process(**defaults)
        s.add(proc)
        s.commit()
        return proc.id


class TestDualWrite:
    """commit_session_with_retry sincroniza Process→PipelineItem."""

    def test_status_change_syncs(self, db):
        pid = _make_promoted(db, status=ProcessStatus.descargada)
        with Session(db) as s:
            proc = s.get(Process, pid)
            proc.status = ProcessStatus.analizada
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.status == ProcessStatus.analizada

    def test_data_dir_syncs(self, db):
        pid = _make_promoted(db)
        with Session(db) as s:
            proc = s.get(Process, pid)
            proc.data_dir = "/data/new_path"
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.data_dir == "/data/new_path"

    def test_interest_status_syncs(self, db):
        pid = _make_promoted(db)
        with Session(db) as s:
            proc = s.get(Process, pid)
            proc.interest_status = InterestStatus.opportunity
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.interest_status == InterestStatus.opportunity

    def test_watch_fields_sync(self, db):
        pid = _make_promoted(db)
        with Session(db) as s:
            proc = s.get(Process, pid)
            proc.watch_unread = True
            proc.watch_changelog_json = '[{"t":"test"}]'
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.watch_unread is True
            assert pi.watch_changelog_json == '[{"t":"test"}]'

    def test_feed_pure_not_synced(self, db):
        """Feed-pure (promoted_at=None) no genera PipelineItem."""
        with Session(db) as s:
            entity = Entity(ruc="12345678901", nombre="Test", activa=True)
            s.add(entity)
            s.flush()
            proc = Process(
                entity_id=entity.id, anio=2026, source="seace",
                source_ref="S002", nomenclatura="LP-02-2026",
                status=ProcessStatus.publicada, promoted_at=None,
            )
            s.add(proc)
            commit_session_with_retry(s)
        with Session(db) as s:
            count = s.query(PipelineItem).count()
            assert count == 0

    def test_analysis_pipeline_item_id_synced(self, db):
        pid = _make_promoted(db, status=ProcessStatus.analizada)
        with Session(db) as s:
            proc = s.get(Process, pid)
            ar = AnalysisResult(process_id=pid, status="done", alcance="test")
            s.add(ar)
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            ar = s.query(AnalysisResult).filter_by(process_id=pid).one()
            assert ar.pipeline_item_id == pi.id

    def test_multiple_updates_same_item(self, db):
        """Múltiples actualizaciones al mismo item no crean duplicados."""
        pid = _make_promoted(db)
        for status in [ProcessStatus.analizada, ProcessStatus.portafolio, ProcessStatus.archivada]:
            with Session(db) as s:
                proc = s.get(Process, pid)
                proc.status = status
                commit_session_with_retry(s)
        with Session(db) as s:
            count = s.query(PipelineItem).filter_by(origin_feed_id=pid).count()
            assert count == 1
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.status == ProcessStatus.archivada

    def test_origin_source_ref_captured(self, db):
        pid = _make_promoted(db, source_ref="REF-XYZ")
        with Session(db) as s:
            proc = s.get(Process, pid)
            proc.data_dir = "/data/updated"
            commit_session_with_retry(s)
        with Session(db) as s:
            pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
            assert pi.origin_source == "seace"
            assert pi.origin_source_ref == "REF-XYZ"
