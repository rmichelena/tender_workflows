"""Tests para nombres de documentos descargados."""

import json
from pathlib import Path

from ..document_storage import (
    allocate_unique_path,
    commit_downloaded_file,
    looks_like_size_label,
    looks_like_uuid_filename,
    normalize_legacy_filenames,
    prepare_download_dest,
    resolve_existing_download,
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


def test_sanitize_preserves_seace_archivo_name():
    raw = "Bases_LPA+0012026F_20260518_123248_485.pdf"
    name = sanitize_download_filename(raw, "uuid-1")
    assert name == raw


def test_looks_like_size_label():
    assert looks_like_size_label("(2646 KB)")
    assert looks_like_size_label("2646 KB")
    assert not looks_like_size_label("Bases_LPA+0012026F.pdf")


def test_looks_like_uuid_filename():
    uuid = "1d8021e2-b946-4077-adc0-984f5332effb"
    assert looks_like_uuid_filename(f"{uuid}.pdf", uuid)
    assert not looks_like_uuid_filename("Bases.pdf", uuid)


def test_index_downloaded_by_uuid_missing_dir(tmp_path: Path):
    missing = tmp_path / "documentos"
    assert missing.is_dir() is False
    from ..document_storage import index_downloaded_by_uuid

    assert index_downloaded_by_uuid(missing) == {}


def test_resolve_existing_ignores_bad_parser_nombre(tmp_path: Path):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    uuid = "85b414d9-b722-41dd-ab61-f5dac6490b05"
    good = docs_dir / "Bases_LPA+0012026F_20260518_123248_485.pdf"
    good.write_bytes(b"pdf")
    (docs_dir / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "uuid": uuid,
                    "nombre": good.name,
                    "archivo": good.name,
                }
            ]
        ),
        encoding="utf-8",
    )
    doc = {"uuid": uuid, "nombre": "(2646 KB)"}
    assert resolve_existing_download(docs_dir, doc) == good
    dest, exists = prepare_download_dest(docs_dir, doc)
    assert exists is True
    assert dest == good
    assert doc["nombre"] == good.name


def test_commit_downloaded_file_uses_content_disposition_name(tmp_path: Path):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    uuid = "85b414d9-b722-41dd-ab61-f5dac6490b05"
    temp = docs_dir / f"{uuid}.download"
    temp.write_bytes(b"pdf")
    doc = {"uuid": uuid, "nombre": "(2646 KB)"}
    final = commit_downloaded_file(
        docs_dir,
        doc,
        temp,
        "Bases_LPA+0012026F_20260518_123248_485.pdf",
    )
    assert final.name == "Bases_LPA+0012026F_20260518_123248_485.pdf"
    assert doc["nombre"] == "Bases_LPA+0012026F_20260518_123248_485.pdf"
    assert doc["archivo"] == final.name


def test_commit_downloaded_file_prefers_alfresco_name_over_uuid_fallback(
    tmp_path: Path,
):
    docs_dir = tmp_path / "documentos"
    docs_dir.mkdir()
    uuid = "1d8021e2-b946-4077-adc0-984f5332effb"
    temp = docs_dir / f"{uuid}.download"
    temp.write_bytes(b"pdf")
    doc = {"uuid": uuid, "nombre": "(2646 KB)"}
    alfresco = "BASES_SERVIDORES_20260513_180726_833.pdf"
    final = commit_downloaded_file(docs_dir, doc, temp, alfresco)
    assert final.name == alfresco
    assert doc["nombre"] == alfresco
    assert not final.name.startswith(uuid)


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
