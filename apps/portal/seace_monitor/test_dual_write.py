"""Tests para dual-write 0.3e-2: FeedItem promovido → PipelineItem auto-sync."""

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from seace_monitor.db.models import (
    AnalysisResult,
    Base,
    Entity,
    PipelineItem,
    FeedItem,
    ProcessStatus,
    InterestStatus,
    utcnow,
)
from seace_monitor.db.session import init_db, session_factory


@pytest.fixture
def db(tmp_path):
    """DB con init_db (registra before_flush event para dual-write)."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    from seace_monitor.db import session as session_mod
    return session_mod._engine


def _make_promoted(eng, **overrides):
    """Crea entity + process promoted usando la session factory."""
    from seace_monitor.db.session import session_factory
    s = session_factory()
    try:
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
        proc = FeedItem(**defaults)
        s.add(proc)
        s.commit()
        return proc.id
    finally:
        s.close()


class TestDualWrite:
    """El before_flush event sincroniza FeedItem→PipelineItem en todos los commits."""

    def test_status_change_syncs(self, db):
        pid = _make_promoted(db, status=ProcessStatus.descargada)
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            proc.status = ProcessStatus.analizada
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.status == ProcessStatus.analizada
        s.close()

    def test_data_dir_syncs(self, db):
        pid = _make_promoted(db)
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            proc.data_dir = "/data/new_path"
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.data_dir == "/data/new_path"
        s.close()

    def test_interest_status_syncs(self, db):
        pid = _make_promoted(db)
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            proc.interest_status = InterestStatus.opportunity
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.interest_status == InterestStatus.opportunity
        s.close()

    def test_watch_fields_sync(self, db):
        pid = _make_promoted(db)
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            proc.watch_unread = True
            proc.watch_changelog_json = '[{"t":"test"}]'
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.watch_unread is True
        assert pi.watch_changelog_json == '[{"t":"test"}]'
        s.close()

    def test_feed_pure_not_synced(self, tmp_path):
        """Feed-pure (promoted_at=None) no genera PipelineItem."""
        db_url = f"sqlite:///{tmp_path / 'test2.db'}"
        init_db(db_url)
        s = session_factory()
        try:
            entity = Entity(ruc="12345678901", nombre="Test", activa=True)
            s.add(entity)
            s.flush()
            proc = FeedItem(
                entity_id=entity.id, anio=2026, source="seace",
                source_ref="S002", nomenclatura="LP-02-2026",
                status=ProcessStatus.publicada, promoted_at=None,
            )
            s.add(proc)
            s.commit()
        finally:
            s.close()
        s = session_factory()
        count = s.query(PipelineItem).count()
        assert count == 0
        s.close()

    def test_analysis_pipeline_item_id_synced(self, db):
        pid = _make_promoted(db, status=ProcessStatus.analizada)
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            ar = AnalysisResult(process_id=pid, status="done", alcance="test")
            s.add(ar)
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        ar = s.query(AnalysisResult).filter_by(process_id=pid).one()
        assert ar.pipeline_item_id == pi.id
        s.close()

    def test_multiple_updates_same_item(self, db):
        """Múltiples actualizaciones al mismo item no crean duplicados."""
        pid = _make_promoted(db)
        for status in [ProcessStatus.analizada, ProcessStatus.portafolio, ProcessStatus.archivada]:
            s = session_factory()
            try:
                proc = s.get(FeedItem, pid)
                proc.status = status
                s.commit()
            finally:
                s.close()
        s = session_factory()
        count = s.query(PipelineItem).filter_by(origin_feed_id=pid).count()
        assert count == 1
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.status == ProcessStatus.archivada
        s.close()

    def test_origin_source_ref_captured(self, db):
        pid = _make_promoted(db, source_ref="REF-XYZ")
        s = session_factory()
        try:
            proc = s.get(FeedItem, pid)
            proc.data_dir = "/data/updated"
            s.commit()
        finally:
            s.close()
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.origin_source == "seace"
        assert pi.origin_source_ref == "REF-XYZ"
        s.close()

    def test_new_promoted_process_without_prior_flush(self, db):
        """Nuevo FeedItem promovido creado y commiteado sin flush previo → PipelineItem correcto."""
        s = session_factory()
        try:
            entity = Entity(ruc="99999999999", nombre="FlushTest", activa=True)
            s.add(entity)
            s.flush()
            proc = FeedItem(
                entity_id=entity.id, anio=2026, source="seace",
                source_ref="FLUSH-1", nomenclatura="LP-FLUSH-2026",
                status=ProcessStatus.descargada, promoted_at=utcnow(),
                data_dir="/data/flush-test",
            )
            s.add(proc)
            s.commit()
            pid = proc.id
        finally:
            s.close()
        # Verify PipelineItem created with correct origin_feed_id
        s = session_factory()
        pi = s.query(PipelineItem).filter_by(origin_feed_id=pid).one()
        assert pi.origin_feed_id == pid
        assert pi.data_dir == "/data/flush-test"
        assert pi.status == ProcessStatus.descargada
        s.close()
