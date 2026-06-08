from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus
from .db.session import session_factory
from .portfolio_workspace import prepare_portfolio_workspace
from .web.app import create_app


def _seed_portfolio_process(tmp_path: Path) -> int:
    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    docs_dir = proc_dir / "documentos"
    docs_dir.mkdir(parents=True)
    (docs_dir / "bases.pdf").write_bytes(b"%PDF-1.4\nbases")
    (docs_dir / "anexo.docx").write_bytes(b"docx")
    (proc_dir / "fast_analysis").mkdir()
    (proc_dir / "fast_analysis" / "analyzed_files.json").write_text(
        json.dumps(["bases.pdf"]),
        encoding="utf-8",
    )

    db: Session = session_factory()
    try:
        entity = Entity(ruc="20123456789", nombre="ENTIDAD TEST", activa=True)
        db.add(entity)
        db.flush()
        proc = Process(
            entity_id=entity.id,
            anio=2026,
            nid_proceso="web-portfolio",
            source_ref="web-portfolio",
            nomenclatura="LP-PORT",
            status=ProcessStatus.portafolio,
            objeto="Bienes",
            descripcion="Compra de equipos",
            data_dir=str(proc_dir),
            documentos_json=json.dumps(
                [
                    {"nombre": "bases.pdf", "archivo": "bases.pdf", "uuid": "doc-1"},
                    {"nombre": "anexo.docx", "archivo": "anexo.docx", "uuid": "doc-2"},
                ]
            ),
        )
        db.add(proc)
        db.commit()
        return proc.id
    finally:
        db.close()


def test_prepare_portfolio_workspace_writes_manifest_context_and_seed(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'pf.db'}")
    create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    db: Session = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        manifest = prepare_portfolio_workspace(
            cfg,
            proc,
            ["bases.pdf"],
            notes="Priorizar plazo de entrega.",
            prepared_by="pytest",
        )
    finally:
        db.close()

    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    portfolio_dir = proc_dir / "portafolio"
    assert (portfolio_dir / "inputs" / "bases.pdf").read_bytes() == b"%PDF-1.4\nbases"
    assert manifest["selected_documents"][0]["dest_path"] == "portafolio/inputs/bases.pdf"
    assert (portfolio_dir / "staging_manifest.json").is_file()
    assert (portfolio_dir / "context.json").is_file()
    seed = (portfolio_dir / "seed_prompt.md").read_text(encoding="utf-8")
    assert "trabajo es agéntico e interactivo" in seed
    assert "Priorizar plazo de entrega." in seed


def test_portfolio_prepare_route_generates_workspace(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web.db'}")
    app = create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    client = TestClient(app)
    get_response = client.get(f"/analizados/{process_id}/portafolio/preparar")
    assert get_response.status_code == 200
    assert "Preparar workspace" in get_response.text

    post_response = client.post(
        f"/analizados/{process_id}/portafolio/preparar",
        data={"selected_files": ["bases.pdf"], "notes": "Seed desde test."},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == f"/analizados/{process_id}?msg=portafolio_preparado"

    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    manifest = json.loads(
        (proc_dir / "portafolio" / "staging_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["process_id"] == process_id
    assert manifest["selected_documents"][0]["source_path"] == "documentos/bases.pdf"
    assert (proc_dir / "portafolio" / "seed_prompt.md").is_file()
