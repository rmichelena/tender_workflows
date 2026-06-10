"""Tests para selección de archivos y descargas en detalle."""

from __future__ import annotations

import json
from pathlib import Path

from .analysis.document_prep import validate_gemini_upload_size
from .db.models import FeedItem
from .web.detail_data import (
    _assign_default_selection,
    build_document_tree,
    count_document_nodes,
    flatten_selectable_leaves,
    load_analysis_selection,
    load_analyzed_files,
    media_type_for_path,
    save_analysis_selection,
    save_analyzed_files,
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
    rows = _assign_default_selection(rows)
    assert rows[0].default_checked is False
    assert rows[1].default_checked is True


def test_save_and_load_analysis_selection(tmp_path: Path):
    save_analysis_selection(tmp_path, ["a.pdf", "_extracted/x/bases.docx"])
    loaded = load_analysis_selection(tmp_path)
    assert loaded == {"a.pdf", "_extracted/x/bases.docx"}


def test_save_and_load_analyzed_files(tmp_path: Path):
    save_analyzed_files(tmp_path, ["bases.pdf", "_extracted/x/anexo.docx"])
    loaded = load_analyzed_files(tmp_path)
    assert loaded == {"bases.pdf", "_extracted/x/anexo.docx"}


def test_load_analyzed_files_falls_back_to_selected(tmp_path: Path):
    save_analysis_selection(tmp_path, ["legacy.pdf"])
    loaded = load_analyzed_files(tmp_path)
    assert loaded == {"legacy.pdf"}


def test_build_document_tree_marks_analyzed_paths(tmp_path: Path):
    docs = tmp_path / "documentos"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"%PDF")
    (docs / "b.pdf").write_bytes(b"%PDF")

    proc = FeedItem(
        id=1,
        data_dir=str(tmp_path),
        documentos_json=json.dumps(
            [
                {"uuid": "u1", "nombre": "a.pdf", "archivo": "a.pdf"},
                {"uuid": "u2", "nombre": "b.pdf", "archivo": "b.pdf"},
            ]
        ),
    )
    tree = build_document_tree(
        proc,
        apply_default_selection=False,
        analyzed_paths={"a.pdf"},
    )
    by_path = {node.rel_path: node for node in tree}
    assert by_path["a.pdf"].analyzed is True
    assert by_path["b.pdf"].analyzed is False


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

    proc = FeedItem(id=1, data_dir=str(tmp_path), documentos_json="[]")
    save_analysis_selection(tmp_path, ["b.docx"])
    rows = list_analyzable_files(proc, checked_paths={"b.docx"})
    checked = [row.rel_path for row in rows if row.default_checked]
    assert checked == ["b.docx"]


def test_build_document_tree_nests_zip_contents(tmp_path: Path):
    docs = tmp_path / "documentos"
    docs.mkdir()
    (docs / "bases.zip").write_bytes(b"PK")
    extract = docs / "_extracted" / "bases"
    extract.mkdir(parents=True)
    (extract / "inner.pdf").write_bytes(b"%PDF")
    (extract / "inner.docx").write_bytes(b"docx")

    proc = FeedItem(
        id=1,
        data_dir=str(tmp_path),
        documentos_json=json.dumps(
            [{"uuid": "u1", "nombre": "bases.zip", "archivo": "bases.zip"}]
        ),
    )
    tree = build_document_tree(proc, apply_default_selection=False)
    assert len(tree) == 1
    assert tree[0].rel_path == "bases.zip"
    assert tree[0].selectable is False
    assert tree[0].previewable is False
    child_paths = {child.rel_path for child in tree[0].children}
    assert child_paths == {
        "_extracted/bases/inner.pdf",
        "_extracted/bases/inner.docx",
    }
    assert count_document_nodes(tree) == 3
    selectable = flatten_selectable_leaves(tree)
    assert {row.rel_path for row in selectable} == child_paths
