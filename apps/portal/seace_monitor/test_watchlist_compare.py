"""Tests comparación semántica watchlist."""

from __future__ import annotations

import json

from .watchlist_compare import (
    cronograma_semantically_equal,
    documentos_semantically_equal,
    watchlist_content_changed,
)


def test_documentos_equal_ignores_fecha_backfill_and_tamano():
    old = json.dumps(
        [{"uuid": "u1", "nombre": "a.pdf", "tamano_kb": "100", "archivo": "a.pdf"}]
    )
    new = json.dumps(
        [
            {
                "uuid": "u1",
                "nombre": "a.pdf",
                "tamano_kb": "101",
                "fecha_publicacion": "18/05/2026 12:34",
            }
        ]
    )
    assert documentos_semantically_equal(old, new)


def test_documentos_equal_ignores_parser_nombre_noise():
    old = json.dumps(
        [
            {
                "uuid": "u1",
                "nombre": "Bases_LPA+0012026F_20260518_123248_485.pdf",
                "etapa": "Convocatoria",
                "tipo_documento": "Bases Administrativas",
            }
        ]
    )
    new = json.dumps(
        [
            {
                "uuid": "u1",
                "nombre": "(2646 KB)",
                "etapa": "Convocatoria",
                "tipo_documento": "Bases Administrativas",
            }
        ]
    )
    assert documentos_semantically_equal(old, new)


def test_documentos_detect_new_uuid():
    a = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}])
    b = json.dumps(
        [{"uuid": "u1", "nombre": "a.pdf"}, {"uuid": "u2", "nombre": "b.pdf"}]
    )
    assert not documentos_semantically_equal(a, b)


def test_cronograma_equal_ignores_etapa_noise():
    a = json.dumps([{"etapa": "Consultas ", "fecha_inicio": "1", "fecha_fin": "2"}])
    b = json.dumps([{"etapa": "Consultas", "fecha_inicio": "1", "fecha_fin": "2"}])
    assert cronograma_semantically_equal(a, b)


def test_watchlist_content_changed_flags_real_doc_addition():
    old_docs = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}])
    new_docs = json.dumps(
        [{"uuid": "u1", "nombre": "a.pdf"}, {"uuid": "u2", "nombre": "b.pdf"}]
    )
    cron, docs = watchlist_content_changed(
        cronograma_json="[]",
        documentos_json=old_docs,
        new_cronograma_json="[]",
        new_documentos_json=new_docs,
    )
    assert cron is False
    assert docs is True
