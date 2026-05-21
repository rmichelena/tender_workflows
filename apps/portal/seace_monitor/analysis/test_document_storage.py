"""Tests para nombres de documentos descargados."""

from pathlib import Path

from ..document_storage import (
    allocate_unique_path,
    normalize_legacy_filenames,
    sanitize_download_filename,
)


def test_sanitize_keeps_readable_name():
    name = sanitize_download_filename(
        "Bases__de_Licitacion_publica_abreviada_para_bienes 2 1.pdf",
        "uuid-1",
    )
    assert name.endswith(".pdf")
    assert "Bases" in name
    assert "uuid" not in name.lower()


def test_sanitize_strips_invalid_chars():
    name = sanitize_download_filename('INFORME N° 002/2026:CS "LPA".pdf', "u")
    assert "<" not in name
    assert ">" not in name
    assert ":" not in name


def test_allocate_unique_path(tmp_path: Path):
    first = allocate_unique_path(tmp_path, "bases.pdf")
    first.write_bytes(b"x")
    second = allocate_unique_path(tmp_path, "bases.pdf")
    assert second.name == "bases_2.pdf"


def test_normalize_legacy_uuid_filename(tmp_path: Path):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    uuid = "4dd843e9-845a-47c1-8692-a9e700e49fef"
    legacy = docs_dir / f"{uuid}.pdf"
    legacy.write_bytes(b"pdf")
    docs = [
        {
            "uuid": uuid,
            "nombre": "INFORME N 0022026CS LPA.030.2025.CORPAC S.A.1.pdf",
        }
    ]
    normalize_legacy_filenames(docs_dir, docs)
    assert not legacy.exists()
    assert docs[0]["archivo"].endswith(".pdf")
    assert "INFORME" in docs[0]["archivo"]
    assert (docs_dir / docs[0]["archivo"]).is_file()


def test_normalize_keeps_existing_readable_filename(tmp_path: Path):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    uuid = "0a600639-6ec0-49bc-a615-a99de65dbf66"
    nombre = "BASES_EETT_LPB_01_2026.zip"
    dest = docs_dir / nombre
    dest.write_bytes(b"zip")
    docs = [
        {
            "uuid": uuid,
            "nombre": nombre,
            "archivo": nombre,
        }
    ]
    normalize_legacy_filenames(docs_dir, docs)
    assert docs[0]["archivo"] == nombre
    assert dest.is_file()
    assert not (docs_dir / "BASES_EETT_LPB_01_2026_2.zip").exists()
