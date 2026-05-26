"""Tests de re-análisis y rollback del resultado previo."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .analysis.runner import AnalysisRunner
from .config import AppConfig
from .db.models import AnalysisResult, Base, Entity, Process, ProcessStatus


@pytest.fixture
def analysis_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20100000001", nombre="Test", activa=True)
    session.add(entity)
    session.flush()
    yield session
    session.close()


def test_analyze_restores_done_analysis_when_prior_snapshot_provided(
    analysis_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc_dir = tmp_path / "proc"
    docs_dir = proc_dir / "documentos"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "bases.pdf"
    doc.write_bytes(b"pdf")
    (docs_dir / "manifest.json").write_text(
        json.dumps(
            [{"uuid": "u1", "nombre": "bases.pdf", "archivo": "bases.pdf"}]
        ),
        encoding="utf-8",
    )
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="nid-1",
        nomenclatura="T-1",
        status=ProcessStatus.analizada,
        documentos_json=json.dumps([{"uuid": "u1", "nombre": "bases.pdf"}]),
        data_dir=str(proc_dir),
    )
    analysis_session.add(proc)
    analysis_session.flush()
    analysis = AnalysisResult(
        process_id=proc.id,
        status="done",
        alcance="Alcance previo",
        run_id="old-run",
    )
    analysis_session.add(analysis)
    analysis_session.commit()

    prior = AnalysisRunner._analysis_snapshot(analysis)
    AnalysisRunner._mark_analysis_running(analysis, "new-run")
    proc.status = ProcessStatus.descargada
    analysis_session.commit()

    runner = AnalysisRunner(cfg, analysis_session)
    with patch.object(
        runner, "_run_pipeline", side_effect=RuntimeError("pipeline failed")
    ):
        with pytest.raises(RuntimeError, match="pipeline failed"):
            runner.analyze(
                proc.id,
                ["bases.pdf"],
                run_id="new-run",
                prior_snapshot=prior,
            )

    analysis_session.refresh(analysis)
    analysis_session.refresh(proc)
    assert analysis.status == "done"
    assert analysis.alcance == "Alcance previo"
    assert proc.status == ProcessStatus.analizada
