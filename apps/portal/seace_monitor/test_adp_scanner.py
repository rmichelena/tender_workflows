"""Tests para el cliente HTTP y scanner ADP."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .adp_client import AdpClient, ALL_WORK_IDS
from .adp_parser import parse_adp_html
from .adp_scanner import (
    ADP_ENTITY_RUC,
    ADP_PORTAL_SOURCE,
    AdpScanner,
    _adp_doc_to_dict,
    _adp_process_to_cronograma,
    _ensure_adp_entity,
)


class TestAdpClient:
    def test_fetch_category_rejects_invalid_work_id(self):
        client = AdpClient()
        with pytest.raises(ValueError, match="work_id inválido"):
            client.fetch_category_html(99)

    def test_all_work_ids(self):
        assert sorted(ALL_WORK_IDS) == [1, 2, 3, 4]


class TestAdpDocConversion:
    def test_to_dict_fields(self):
        from .adp_parser import AdpDocument
        doc = AdpDocument(
            title="BASES DEL PROCESO",
            vigencia_desde="01.01.26",
            vigencia_hasta="31.12.26",
            download_url="https://www.adp.com.pe/Web/getFile?name_file=abc.pdf&new_name=bases.pdf",
            name_file="abc.pdf",
            new_name="bases.pdf",
        )
        d = _adp_doc_to_dict(doc)
        assert d["name_file"] == "abc.pdf"
        assert d["new_name"] == "bases.pdf"
        assert d["title"] == "BASES DEL PROCESO"
        assert d["uuid"] == "abc.pdf"
        assert d["archivo"] == ""


class TestAdpCronograma:
    def test_extracts_dates(self):
        from .adp_parser import AdpDocument, AdpProcess
        docs = [
            AdpDocument(title="BASES", vigencia_desde="01.01.26", vigencia_hasta="31.12.26",
                        download_url="https://x.com/a.pdf", name_file="a.pdf", new_name="a.pdf"),
            AdpDocument(title="CIRCULAR", vigencia_desde="15.03.26", vigencia_hasta="15.04.26",
                        download_url="https://x.com/b.pdf", name_file="b.pdf", new_name="b.pdf"),
        ]
        proc = AdpProcess(code="LPN-001-2026-ADP", description="test", work_id=3, documents=docs)
        cron = _adp_process_to_cronograma(proc)
        assert len(cron) == 2
        assert cron[0]["titulo"] == "BASES"
        assert cron[0]["fecha_desde"] == "01.01.26"

    def test_skips_docs_without_dates(self):
        from .adp_parser import AdpDocument, AdpProcess
        docs = [
            AdpDocument(title="SIN FECHA", vigencia_desde=None, vigencia_hasta=None,
                        download_url="https://x.com/a.pdf", name_file="a.pdf", new_name="a.pdf"),
        ]
        proc = AdpProcess(code="LPN-001-2026-ADP", description="test", work_id=3, documents=docs)
        cron = _adp_process_to_cronograma(proc)
        assert len(cron) == 0


class TestExtractAnio:
    def test_standard_code(self):
        assert AdpScanner._extract_anio("LPN-003-2026-ADP") == 2026

    def test_code_without_year(self):
        # Si no hay año, usa el año actual
        from datetime import datetime
        result = AdpScanner._extract_anio("PROCESS-NO-YEAR")
        assert result == datetime.now().year

    def test_code_with_2025(self):
        assert AdpScanner._extract_anio("LPI-001-2025-ADP") == 2025


class TestEnsureAdpEntity:
    def test_creates_entity_if_missing(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = None
        entity = _ensure_adp_entity(session)
        assert entity.ruc == ADP_ENTITY_RUC
        assert entity.nombre == "Aeropuertos del Perú"
        session.add.assert_called_once()

    def test_returns_existing_entity(self):
        existing = MagicMock()
        existing.ruc = ADP_ENTITY_RUC
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = existing
        entity = _ensure_adp_entity(session)
        assert entity is existing
        session.add.assert_not_called()


class TestIngestRegistry:
    def test_adp_registered(self):
        from .ingest import get_adapter, registered_sources
        sources = registered_sources()
        assert "adp_portal" in sources
        adapter = get_adapter("adp_portal")
        assert adapter.label == "ADP Portal"
        assert adapter.capabilities.scan_listings is True
