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


def test_search_returns_page_zero_match_without_scanning_all_pages():
    page0 = object()
    row = _row("new-nid", link_id="fresh-link", nomenclatura="LP-X")
    client = MagicMock()
    client.fetch_list_page.return_value = ("", page0)
    client.total_pages.return_value = 67
    client.parse_rows.return_value = [row]

    found = search_list_row_by_nomenclatura(client, "LP-X")

    assert found is row
    client.fetch_list_page.assert_called_once_with(0)


def test_search_prefers_page_zero_over_later_duplicate():
    """Reconvocatoria en páginas antiguas no debe ganar sobre la fila en página 0."""
    page0 = object()
    page5 = object()
    stale = _row("old-nid", link_id="stale-link", nomenclatura="LP-X")
    fresh = _row("new-nid", link_id="fresh-link", nomenclatura="LP-X")
    client = MagicMock()
    client.fetch_list_page.side_effect = [("", page0)]
    client.total_pages.return_value = 6
    client.parse_rows.side_effect = [[fresh]]

    row = search_list_row_by_nomenclatura(client, "LP-X")

    assert row is fresh
    client.fetch_list_page.assert_called_once_with(0)


def test_search_stops_at_first_match_on_page_without_scanning_rest():
    page0 = object()
    page1 = object()
    row = _row("target-nid", link_id="fresh-link", nomenclatura="T-target")
    client = MagicMock()
    client.fetch_list_page.side_effect = [("", page0), ("", page1)]
    client.total_pages.return_value = 5
    client.parse_rows.side_effect = [[_row("other", nomenclatura="OTHER")], [row]]

    found = search_list_row_by_nomenclatura(client, "T-target")

    assert found is row
    assert client.fetch_list_page.call_count == 2


def test_search_prefers_top_row_on_same_page():
    fresh = _row("new-nid", link_id="fresh-link", nomenclatura="LP-X")
    stale = _row("old-nid", link_id="stale-link", nomenclatura="LP-X")
    client = MagicMock()
    client.fetch_list_page.return_value = ("", object())
    client.total_pages.return_value = 1
    client.parse_rows.return_value = [fresh, stale]

    row = search_list_row_by_nomenclatura(client, "LP-X")

    assert row is fresh


def test_search_finds_row_on_later_page_when_not_on_page_zero():
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
