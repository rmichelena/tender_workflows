"""Tests historial watchlist."""

from __future__ import annotations

import json

from .watchlist_changelog import build_watchlist_changelog_entry


def test_changelog_cronograma_fin_change():
    old_cron = json.dumps(
        [
            {
                "etapa": "Presentación de propuestas",
                "fecha_inicio": "01/01/2026",
                "fecha_fin": "10/01/2026",
            }
        ]
    )
    new_cron = json.dumps(
        [
            {
                "etapa": "Presentación de propuestas",
                "fecha_inicio": "01/01/2026",
                "fecha_fin": "15/01/2026",
            }
        ]
    )
    entry = build_watchlist_changelog_entry(
        old_cronograma_json=old_cron,
        new_cronograma_json=new_cron,
        old_documentos_json="[]",
        new_documentos_json="[]",
        old_fecha_publicacion=None,
        new_fecha_publicacion=None,
    )
    assert entry["changes"]
    assert entry["changes"][0]["kind"] == "modified"
    assert entry["changes"][0]["old"] == "10/01/2026"
    assert entry["changes"][0]["new"] == "15/01/2026"


def test_changelog_new_document():
    old_docs = json.dumps([])
    new_docs = json.dumps(
        [
            {
                "uuid": "u1",
                "etapa": "Convocatoria",
                "tipo_documento": "Bases Administrativas",
            }
        ]
    )
    entry = build_watchlist_changelog_entry(
        old_cronograma_json="[]",
        new_cronograma_json="[]",
        old_documentos_json=old_docs,
        new_documentos_json=new_docs,
        old_fecha_publicacion=None,
        new_fecha_publicacion=None,
    )
    assert any(c["kind"] == "added" for c in entry["changes"])
