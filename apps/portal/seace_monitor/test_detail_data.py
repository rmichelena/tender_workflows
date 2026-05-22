"""Tests para selección de archivos y descargas en detalle."""

from __future__ import annotations

from pathlib import Path

from .analysis.document_prep import validate_gemini_upload_size
from .db.models import Process
from .web.detail_data import (
    _assign_default_selection,
    load_analysis_selection,
    media_type_for_path,
    save_analysis_selection,
    list_analyzable_files,
    ArchivoAnalizable,
)


def test_media_type_for_docx():
    assert (
        media_type_for_path(Path("bases.docx"))
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_media_type_for_pdf():
    assert media_type_for_path(Path("bases.pdf")) == "application/pdf"


def test_assign_default_selection_picks_single_bases_pdf():
    rows = [
        ArchivoAnalizable(
            rel_path="a.docx",
            nombre="BASES - SDWAN vRevisado.docx",
            extension="docx",
            icon="docx",
            size_label="1 KB",
            origen="extraído",
            tipo_documento="",
            default_checked=False,
        ),
        ArchivoAnalizable(
            rel_path="b.pdf",
            nombre="BASES FIRMADAS RED SDWAN.pdf",
            extension="pdf",
            icon="pdf",
            size_label="1 KB",
            origen="extraído",
            tipo_documento="",
            default_checked=False,
        ),
    ]
    _assign_default_selection(rows)
    assert rows[0].default_checked is False
    assert rows[1].default_checked is True


def test_save_and_load_analysis_selection(tmp_path: Path):
    save_analysis_selection(tmp_path, ["a.pdf", "_extracted/x/bases.docx"])
    loaded = load_analysis_selection(tmp_path)
    assert loaded == {"a.pdf", "_extracted/x/bases.docx"}


def test_validate_gemini_upload_size_rejects_large_pdf(tmp_path: Path):
    big = tmp_path / "huge.pdf"
    big.write_bytes(b"x" * (51 * 1024 * 1024))
    try:
        validate_gemini_upload_size(big)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "MB" in str(exc)
        assert "DOCX" in str(exc)


def test_list_analyzable_files_uses_saved_selection(tmp_path: Path):
    docs = tmp_path / "documentos"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"%PDF-1.4")
    (docs / "b.docx").write_bytes(b"docx")

    proc = Process(id=1, data_dir=str(tmp_path), documentos_json="[]")
    save_analysis_selection(tmp_path, ["b.docx"])
    rows = list_analyzable_files(proc, checked_paths={"b.docx"})
    checked = [row.rel_path for row in rows if row.default_checked]
    assert checked == ["b.docx"]
