"""Tests para validadores y partición Docling/planos del runner step 1."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import fitz
import run_step1_to_1_3 as runner


def test_valid_pdf_accepts_real_pdf(make_min_pdf, tmp_path: Path):
    pdf = make_min_pdf(tmp_path / "ok.pdf")
    assert runner.valid_pdf(pdf)


def test_valid_pdf_rejects_empty_file(tmp_path: Path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert not runner.valid_pdf(empty)


def test_valid_markdown_requires_nonempty_text(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("   \n", encoding="utf-8")
    assert not runner.valid_markdown(md)
    md.write_text("# hello\n\n" + ("contenido " * 20), encoding="utf-8")
    assert runner.valid_markdown(md)


def test_write_step_1_3_outputs_partial_payload(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    d = {"art": art}
    runner.write_step_1_3_outputs(
        d,
        [{"path": "/tmp/a.md", "source_type": "pdf"}],
        pending_planos=[{"stem": "bases"}],
    )
    payload = json.loads((art / "step_1_3_outputs.json").read_text(encoding="utf-8"))
    assert payload["partial"] is True
    assert payload["pending_planos"][0]["stem"] == "bases"
    assert payload["markdown_outputs"] == ["/tmp/a.md"]


def test_audit_partitions_pending_and_ready(make_min_pdf, tmp_path: Path):
    clean_dir = tmp_path / "clean"
    planos_dir = tmp_path / "planos"
    clean_dir.mkdir()
    planos_dir.mkdir()

    pending_pdf = make_min_pdf(clean_dir / "bases_clean.pdf")
    ready_pdf = make_min_pdf(clean_dir / "anexo_clean.pdf")

    (planos_dir / "bases_page_size_audit.json").write_text(
        json.dumps({"candidates": [{"page": 1}]}),
        encoding="utf-8",
    )
    (planos_dir / "anexo_page_size_audit.json").write_text(
        json.dumps({"candidates": []}),
        encoding="utf-8",
    )

    d = {
        "clean": clean_dir,
        "planos": planos_dir,
        "preocr": tmp_path / "preocr",
        "logs": tmp_path / "logs",
        "art": tmp_path / "artifacts",
    }
    d["preocr"].mkdir()
    d["logs"].mkdir()
    d["art"].mkdir()

    with patch.object(runner, "run") as mock_run:
        pending, ready = runner.audit_and_build_plan_pages(
            [pending_pdf, ready_pdf],
            d,
            overwrite=False,
        )

    assert len(pending) == 1
    assert pending[0]["stem"] == "bases"
    assert ready == [ready_pdf]
    mock_run.assert_not_called()
