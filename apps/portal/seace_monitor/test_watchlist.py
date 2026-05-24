"""Tests watchlist SEACE."""

from __future__ import annotations

import json

from .db.models import Process, ProcessStatus
from .watchlist import mark_watchlist_read, watchlist_fingerprint
from .web.detail_data import parse_cronograma


def test_watchlist_fingerprint_detects_document_change():
    cron = json.dumps([{"etapa": "A", "fecha_inicio": "1", "fecha_fin": "2"}])
    docs_a = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}])
    docs_b = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}, {"uuid": "u2", "nombre": "b.pdf"}])
    fp_a = watchlist_fingerprint(cronograma_json=cron, documentos_json=docs_a)
    fp_b = watchlist_fingerprint(cronograma_json=cron, documentos_json=docs_b)
    assert fp_a != fp_b


def test_parse_cronograma_diff():
    current = json.dumps(
        [
            {"etapa": "Consultas", "fecha_inicio": "01/01/26", "fecha_fin": "10/01/26"},
        ]
    )
    prev = json.dumps(
        [
            {"etapa": "Consultas", "fecha_inicio": "01/01/26", "fecha_fin": "05/01/26"},
        ]
    )
    rows = parse_cronograma(current, prev_cronograma_json=prev)
    assert len(rows) == 1
    assert rows[0].changed is True
    assert rows[0].fecha_fin_prev == "05/01/26"
    assert rows[0].fecha_fin == "10/01/26"


def test_mark_watchlist_read_clears_flag():
    proc = Process(
        entity_id=1,
        anio=2026,
        nid_proceso="1",
        nomenclatura="T",
        status=ProcessStatus.descargada,
        watch_unread=True,
        watch_cronograma_prev_json="[]",
        watch_documentos_prev_json="[]",
    )
    mark_watchlist_read(proc)
    assert proc.watch_unread is False
    assert proc.watch_cronograma_prev_json is None
