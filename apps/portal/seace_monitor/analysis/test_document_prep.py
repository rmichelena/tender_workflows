"""Tests ligeros para selección de bases."""

from pathlib import Path

from .document_prep import normalize_doc_name, score_bases_candidate


def test_normalize_mangled_bases_name():
    assert "bases integradas" in normalize_doc_name("Bases+Integradas_2026")


def test_prefers_bases_integradas():
    plain = Path("Bases_de_Licitacion.pdf")
    integrated = Path("bases_integradas_xyz.pdf")
    assert score_bases_candidate(integrated) > score_bases_candidate(plain)
