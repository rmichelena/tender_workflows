from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus
from .db.session import session_factory
from .portfolio_workspace import (
    PortfolioUpload,
    parse_seace_datetime,
    prepare_portfolio_workspace,
)
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
            document_roles={"bases.pdf": "bases_iniciales"},
            notes="Priorizar plazo de entrega.",
            prepared_by="pytest",
        )
    finally:
        db.close()

    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    portfolio_dir = proc_dir / "portafolio"
    assert (portfolio_dir / "inputs" / "bases.pdf").read_bytes() == b"%PDF-1.4\nbases"
    assert manifest["selected_documents"][0]["dest_path"] == "portafolio/inputs/bases.pdf"
    assert manifest["selected_documents"][0]["document_role"] == "bases_iniciales"
    assert manifest["portfolio_scenario"]["id"] == "initial_bases"
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
    assert "Bases iniciales" in get_response.text

    post_response = client.post(
        f"/analizados/{process_id}/portafolio/preparar",
        data={
            "selected_files": ["bases.pdf"],
            "document_role:bases.pdf": "bases_aclaradas",
            "notes": "Seed desde test.",
        },
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
    assert manifest["selected_documents"][0]["document_role"] == "bases_aclaradas"
    assert manifest["portfolio_scenario"]["seed_variant"] == "bases_clarifications_integrated"
    assert (proc_dir / "portafolio" / "seed_prompt.md").is_file()


def test_clarification_role_populates_clarifications_and_seed_variant(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'clar.db'}")
    create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    db: Session = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        manifest = prepare_portfolio_workspace(
            cfg,
            proc,
            ["bases.pdf", "anexo.docx"],
            document_roles={
                "bases.pdf": "bases_iniciales",
                "anexo.docx": "aclaraciones",
            },
        )
    finally:
        db.close()

    assert manifest["portfolio_scenario"]["id"] == "integrate_clarifications"
    assert manifest["portfolio_scenario"]["seed_variant"] == "bases_plus_clarifications"
    assert manifest["clarifications"] == [
        {
            "file": "portafolio/inputs/anexo.docx",
            "clarification_type": "aclaracion",
            "notes": "Clasificado por usuario en staging de portafolio",
        }
    ]


def test_uploads_go_to_uploads_dir_and_participate_in_scenario(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'upload.db'}")
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
            document_roles={"bases.pdf": "bases_iniciales"},
            uploads=[
                PortfolioUpload(
                    filename="EETT cámara térmica.pdf",
                    content=b"%PDF-1.4\nupload",
                    document_role="especificaciones_tecnicas",
                )
            ],
        )
    finally:
        db.close()

    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    assert (proc_dir / "portafolio" / "inputs" / "bases.pdf").is_file()
    upload_path = proc_dir / "portafolio" / "uploads" / "EETT_c_mara_t_rmica.pdf"
    assert upload_path.read_bytes() == b"%PDF-1.4\nupload"
    assert manifest["uploads"][0]["dest_path"] == "portafolio/uploads/EETT_c_mara_t_rmica.pdf"
    assert manifest["uploads"][0]["document_role"] == "especificaciones_tecnicas"
    context = json.loads(
        (proc_dir / "portafolio" / "context.json").read_text(encoding="utf-8")
    )
    assert context["paths"]["uploads_dir"].endswith("/portafolio/uploads")


def test_technical_specs_without_bases_does_not_claim_initial_bases(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'specs.db'}")
    create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    db: Session = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        manifest = prepare_portfolio_workspace(
            cfg,
            proc,
            ["anexo.docx"],
            document_roles={"anexo.docx": "especificaciones_tecnicas"},
        )
    finally:
        db.close()

    scenario = manifest["portfolio_scenario"]
    assert scenario["id"] == "technical_specs_only"
    assert "sin asumir que hay bases iniciales" in scenario["recommended_action"]
    seed = (
        tmp_path
        / "tenants"
        / "default"
        / "procesos"
        / "web-portfolio"
        / "portafolio"
        / "seed_prompt.md"
    ).read_text(encoding="utf-8")
    assert "no clasificó documentos como bases iniciales" in seed


def test_portfolio_prepare_route_accepts_upload_only(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web-upload.db'}")
    app = create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    response = TestClient(app).post(
        f"/analizados/{process_id}/portafolio/preparar",
        data={"upload_role": "especificaciones_tecnicas", "notes": "Solo upload."},
        files=[("upload_files", ("specs.pdf", b"%PDF-1.4\nupload", "application/pdf"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    proc_dir = tmp_path / "tenants" / "default" / "procesos" / "web-portfolio"
    assert not any((proc_dir / "portafolio" / "inputs").iterdir())
    assert (proc_dir / "portafolio" / "uploads" / "specs.pdf").is_file()
    manifest = json.loads(
        (proc_dir / "portafolio" / "staging_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["selected_documents"] == []
    assert manifest["uploads"][0]["document_role"] == "especificaciones_tecnicas"
    assert manifest["portfolio_scenario"]["id"] == "technical_specs_only"


def test_parse_seace_datetime_uses_lima_timezone():
    lima = timezone(timedelta(hours=-5))
    expected = datetime(2026, 6, 8, 10, 30, tzinfo=lima).timestamp()

    assert parse_seace_datetime("08/06/2026 10:30") == expected


def test_reprepare_keeps_distinct_manifest_backups(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'backup.db'}")
    create_app(cfg)
    process_id = _seed_portfolio_process(tmp_path)

    db: Session = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        prepare_portfolio_workspace(
            cfg,
            proc,
            ["bases.pdf"],
            document_roles={"bases.pdf": "bases_iniciales"},
        )
        prepare_portfolio_workspace(
            cfg,
            proc,
            ["anexo.docx"],
            document_roles={"anexo.docx": "otros"},
        )
        prepare_portfolio_workspace(
            cfg,
            proc,
            ["bases.pdf"],
            document_roles={"bases.pdf": "bases_iniciales"},
        )
    finally:
        db.close()

    backup_dir = (
        tmp_path
        / "tenants"
        / "default"
        / "procesos"
        / "web-portfolio"
        / "portafolio"
        / "backups"
    )
    backups = sorted(backup_dir.glob("staging_manifest.json.*.bak"))
    assert len(backups) >= 2
