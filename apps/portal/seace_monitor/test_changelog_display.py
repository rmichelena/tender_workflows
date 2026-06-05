"""Tests display del historial watchlist (W2)."""

from __future__ import annotations

import json

from .watchlist_changelog import (
    build_watchlist_changelog_entry,
    changelog_entry_at_label,
)


def test_document_only_entry_uses_portal_publication_date():
    new_docs = json.dumps(
        [
            {
                "uuid": "u1",
                "etapa": "Convocatoria",
                "tipo_documento": "Bases",
                "fecha_publicacion": "02/06/2026 16:33",
            }
        ]
    )
    entry = build_watchlist_changelog_entry(
        old_cronograma_json="[]",
        new_cronograma_json="[]",
        old_documentos_json="[]",
        new_documentos_json=new_docs,
        old_fecha_publicacion=None,
        new_fecha_publicacion=None,
    )
    assert changelog_entry_at_label(entry["at"], entry["changes"]) == "02/06/2026 16:33"


def test_cronograma_entry_uses_scan_time_in_lima():
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
    # Fijar hora de scan conocida (UTC) → 17:30 Lima (UTC-5) el 05/06/2026.
    entry["at"] = "2026-06-05T22:30:00+00:00"
    assert (
        changelog_entry_at_label(
            entry["at"], entry["changes"], display_timezone="America/Lima"
        )
        == "05/06/2026 17:30"
    )
