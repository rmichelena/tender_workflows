"""Tests de búsqueda SEACE por nomenclatura."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from .client import ProcessRow
from .config import AppConfig
from .db.models import Entity, Process
from .seace_search import (
    normalize_nomenclatura,
    resolve_process_row,
    search_list_row_by_nomenclatura,
)


def _row(nid: str, *, link_id: str = "link", nomenclatura: str = "T-1") -> ProcessRow:
    return ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="",
        nomenclatura=nomenclatura,
        reiniciado_desde="",
        objeto="",
        descripcion="",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso=nid,
        nid_convocatoria="conv-fresh",
        nid_sistema="3",
        link_id=link_id,
        ntipo="0",
    )


def test_normalize_nomenclatura_collapses_whitespace_and_case():
    assert normalize_nomenclatura("  lp-abr-7-2026  ") == "LP-ABR-7-2026"


def test_search_finds_row_on_later_page_scanning_backwards():
    first_soup = object()
    second_soup = object()
    current_row = _row("target-nid", link_id="fresh-link", nomenclatura="T-target")
    client = MagicMock()
    client.fetch_list_page.side_effect = [("", first_soup), ("", second_soup)]
    client.total_pages.return_value = 2
    client.parse_rows.side_effect = [[_row("other-nid", nomenclatura="OTHER")], [current_row]]

    row = search_list_row_by_nomenclatura(client, "T-target")

    assert row is current_row
    assert client.fetch_list_page.call_args_list[0].args == (0,)
    assert client.fetch_list_page.call_args_list[1].args == (1,)


def test_search_prefers_last_duplicate_nomenclatura_on_page():
    stale = _row("old-nid", link_id="stale-link", nomenclatura="LP-X")
    fresh = _row("new-nid", link_id="fresh-link", nomenclatura="LP-X")
    client = MagicMock()
    client.fetch_list_page.return_value = ("", object())
    client.total_pages.return_value = 1
    client.parse_rows.return_value = [stale, fresh]

    row = search_list_row_by_nomenclatura(client, "LP-X")

    assert row is fresh


def test_resolve_process_row_uses_process_nomenclatura():
    entity = Entity(ruc="20100000001", nombre="Test", activa=True)
    proc = Process(
        entity=entity,
        entity_id=1,
        anio=2026,
        nid_proceso="old-nid",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
    )
    continued_row = _row("new-nid", link_id="fresh-link", nomenclatura="LP-ABR-7-2026-BCRPLIM-2")
    stale_row = _row("old-nid", link_id="stale-link", nomenclatura="LP-ABR-7-2026-BCRPLIM-1")
    client = MagicMock()
    client.fetch_list_page.return_value = ("", object())
    client.total_pages.return_value = 1
    client.parse_rows.return_value = [stale_row, continued_row]

    row, _ = resolve_process_row(AppConfig(), proc, client)

    assert row is continued_row


def test_search_rejects_empty_nomenclatura():
    with pytest.raises(RuntimeError, match="Nomenclatura vacía"):
        search_list_row_by_nomenclatura(MagicMock(), "   ")
