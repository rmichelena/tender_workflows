"""Tests del downloader ADP: colisiones de nombre y limpieza en fallo (3)."""

from __future__ import annotations

from pathlib import Path

from .adp_downloader import download_adp_documents


class _FakeClient:
    def __init__(self, fail_for: set[str] | None = None):
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    def download_document(self, name_file: str, dest: Path) -> Path:
        self.calls.append(name_file)
        if name_file in self.fail_for:
            raise RuntimeError("boom")
        dest.write_bytes(b"%PDF-1.4 fake")
        return dest


def test_distinct_docs_same_friendly_name_get_distinct_files(tmp_path: Path):
    docs = [
        {"name_file": "hash-a", "new_name": "BASES.pdf", "title": "Bases"},
        {"name_file": "hash-b", "new_name": "BASES.pdf", "title": "Bases"},
    ]
    client = _FakeClient()

    n = download_adp_documents(tmp_path, docs, client)

    assert n == 2
    archivos = {d["archivo"] for d in docs}
    assert len(archivos) == 2  # no colapsan en un único archivo
    for archivo in archivos:
        assert (tmp_path / archivo).exists()
    assert client.calls == ["hash-a", "hash-b"]


def test_failed_download_cleans_placeholder_and_keeps_others(tmp_path: Path):
    docs = [
        {"name_file": "ok-1", "new_name": "DOC.pdf"},
        {"name_file": "bad", "new_name": "DOC.pdf"},
        {"name_file": "ok-2", "new_name": "DOC.pdf"},
    ]
    client = _FakeClient(fail_for={"bad"})

    n = download_adp_documents(tmp_path, docs, client)

    assert n == 2
    # El doc fallido no deja placeholder vacío ni registra `archivo`.
    assert "archivo" not in docs[1] or not docs[1].get("archivo")
    pdfs = sorted(p.name for p in tmp_path.iterdir())
    assert len(pdfs) == 2  # solo los dos exitosos, sin archivo vacío huérfano
    assert all((tmp_path / d["archivo"]).stat().st_size > 0 for d in (docs[0], docs[2]))


def test_already_downloaded_doc_is_skipped(tmp_path: Path):
    existing = tmp_path / "BASES.pdf"
    existing.write_bytes(b"%PDF old")
    docs = [{"name_file": "hash-a", "new_name": "BASES.pdf", "archivo": "BASES.pdf"}]
    client = _FakeClient()

    n = download_adp_documents(tmp_path, docs, client)

    assert n == 0
    assert client.calls == []
