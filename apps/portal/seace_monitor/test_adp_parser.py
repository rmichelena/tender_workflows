"""Tests para el parser HTML del portal ADP."""

import hashlib
import json
from pathlib import Path

import pytest

from .adp_parser import (
    AdpDocument,
    AdpProcess,
    _fingerprint_payload,
    parse_adp_html,
    parse_vigencia,
)


# ── HTML mínimo para tests ───────────────────────────────────

SAMPLE_HTML = """
<li class="col-sm-12 parent-main-competition">
    <div class="container-agreements container-participations-item container-list-competition-modules container-title-competition">
        <ul>
            <li class="left"><div class="container-icon-file"><img src="/contents/images/icono-file-open.png" /></div></li>
            <li class="col-sm-8 col-xs-8 box-without-padding">
                <div class="container-text-pdf-title">
                    <span class="title">LPN-003-2026-ADP</span>
                    <span class="excerpt">ADQUISICIÓN DE TORRE DE ILUMINACIÓN REMOLCABLE PARA LOS AEROPUERTOS DE CAJAMARCA</span>
                </div>
            </li>
            <li class="box-without-padding">
                <div class="right btn-view-all-competition"><a href="">Ver todos</a></div>
            </li>
        </ul>
        <div class="clear"></div>
    </div>
    <div class="container-competition" style="display:none;">
        <div class="container-agreements container-participations-item container-list-competition-modules">
            <ul>
                <li class="left">
                    <div class="container-icon-file">
                        <a href="/Web/getFile_store_competition_download?name_file=856413_9927115_8631266.pdf&amp;new_name=BASES%20LPI-002-2019-ADP.pdf" data-name="856413_9927115_8631266.pdf" data-new-name="BASES LPI-002-2019-ADP.pdf"><img src="/contents/images/icon-pdf.png" /></a>
                    </div>
                </li>
                <li class="col-sm-10 col-xs-10 box-without-padding">
                    <div class="container-text-pdf-title list-internal">
                        <span class="title">BASES DEL PROCESO DE SELECCIÓN</span>
                        <span class="excerpt">Fecha de vigencia: Desde 12.03.26 Hasta 27.05.26</span>
                    </div>
                </li>
            </ul>
        </div>
        <div class="clear"></div>
        <div class="container-agreements container-participations-item container-list-competition-modules">
            <ul>
                <li class="left">
                    <div class="container-icon-file">
                        <a href="/Web/getFile_store_competition_download?name_file=9643672_7128155_7074572.pdf&amp;new_name=REGISTRO%20DE%20PROVEEDORES.pdf" data-name="9643672_7128155_7074572.pdf" data-new-name="REGISTRO DE PROVEEDORES.pdf"><img src="/contents/images/icon-pdf.png" /></a>
                    </div>
                </li>
                <li class="col-sm-10 col-xs-10 box-without-padding">
                    <div class="container-text-pdf-title list-internal">
                        <span class="title">FORMATO REGISTRO DE PROVEEDORES</span>
                        <span class="excerpt">Fecha de vigencia: Desde 21.02.19 Hasta 08.05.19</span>
                    </div>
                </li>
            </ul>
        </div>
        <div class="clear"></div>
    </div>
</li>
<li class="col-sm-12 parent-main-competition">
    <div class="container-agreements container-participations-item container-list-competition-modules container-title-competition">
        <ul>
            <li class="left"><div class="container-icon-file"><img src="/contents/images/icono-file-open.png" /></div></li>
            <li class="col-sm-8 col-xs-8 box-without-padding">
                <div class="container-text-pdf-title">
                    <span class="title">LPN-001-2026-AdP SEGUNDA CONVOCATORIA</span>
                    <span class="excerpt">SEGUNDA CONVOCATORIA ADQUISICIÓN DE BALANZA CHECK IN</span>
                </div>
            </li>
            <li class="box-without-padding">
                <div class="right btn-view-all-competition"><a href="">Ver todos</a></div>
            </li>
        </ul>
        <div class="clear"></div>
    </div>
    <div class="container-competition" style="display:none;">
        <div class="container-agreements container-participations-item container-list-competition-modules">
            <ul>
                <li class="left">
                    <div class="container-icon-file">
                        <a href="/Web/getFile_store_competition_download?name_file=aaa111_bbb222_ccc333.pdf&amp;new_name=BASES%20LPN-001-2026.pdf" data-name="aaa111_bbb222_ccc333.pdf" data-new-name="BASES LPN-001-2026.pdf"><img src="/contents/images/icon-pdf.png" /></a>
                    </div>
                </li>
                <li class="col-sm-10 col-xs-10 box-without-padding">
                    <div class="container-text-pdf-title list-internal">
                        <span class="title">BASES DEL PROCESO DE SELECCIÓN</span>
                        <span class="excerpt">Fecha de vigencia: Desde 30.03.26 Hasta 10.06.26</span>
                    </div>
                </li>
            </ul>
        </div>
        <div class="clear"></div>
    </div>
</li>
"""


class TestParseVigencia:
    def test_standard_format(self):
        desde, hasta = parse_vigencia("Fecha de vigencia: Desde 12.03.26 Hasta 27.05.26")
        assert desde == "12.03.26"
        assert hasta == "27.05.26"

    def test_none_input(self):
        assert parse_vigencia(None) == (None, None)

    def test_empty_string(self):
        assert parse_vigencia("") == (None, None)

    def test_no_match(self):
        assert parse_vigencia("Algun texto sin vigencia") == (None, None)

    def test_multiline_whitespace(self):
        text = "  Fecha de vigencia:  Desde 01.01.26  Hasta  31.12.26  "
        desde, hasta = parse_vigencia(text)
        assert desde == "01.01.26"
        assert hasta == "31.12.26"


class TestParseAdpHtml:
    def test_extracts_two_processes(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        assert len(processes) == 2

    def test_process_code(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        assert processes[0].code == "LPN-003-2026-ADP"
        assert processes[1].code == "LPN-001-2026-AdP SEGUNDA CONVOCATORIA"

    def test_process_description(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        assert "TORRE DE ILUMINACIÓN" in processes[0].description
        assert "BALANZA CHECK IN" in processes[1].description

    def test_work_id_assigned(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=4)
        assert all(p.work_id == 4 for p in processes)

    def test_documents_count(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        assert len(processes[0].documents) == 2
        assert len(processes[1].documents) == 1

    def test_document_fields(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        doc = processes[0].documents[0]
        assert doc.title == "BASES DEL PROCESO DE SELECCIÓN"
        assert doc.vigencia_desde == "12.03.26"
        assert doc.vigencia_hasta == "27.05.26"
        assert "856413_9927115_8631266.pdf" in doc.download_url
        assert doc.name_file == "856413_9927115_8631266.pdf"
        assert doc.new_name == "BASES LPI-002-2019-ADP.pdf"

    def test_download_url_is_absolute(self):
        processes = parse_adp_html(SAMPLE_HTML, work_id=3)
        for proc in processes:
            for doc in proc.documents:
                assert doc.download_url.startswith("https://")

    def test_empty_html(self):
        assert parse_adp_html("", work_id=3) == []

    def test_process_without_documents(self):
        html = """
        <li class="col-sm-12 parent-main-competition">
            <div class="container-title-competition">
                <ul>
                    <li class="col-sm-8 col-xs-8 box-without-padding">
                        <div class="container-text-pdf-title">
                            <span class="title">LPN-099-2026-ADP</span>
                            <span class="excerpt">PROCESO SIN DOCS</span>
                        </div>
                    </li>
                </ul>
            </div>
        </li>
        """
        processes = parse_adp_html(html, work_id=3)
        assert len(processes) == 1
        assert len(processes[0].documents) == 0


class TestContentHash:
    def test_deterministic(self):
        p1 = AdpProcess(code="LPN-001-2026-ADP", description="test", work_id=3, documents=[])
        p2 = AdpProcess(code="LPN-001-2026-ADP", description="different desc", work_id=3, documents=[])
        # Hash solo depende de code, work_id y documentos, no description
        assert p1.content_hash() == p2.content_hash()

    def test_different_code_different_hash(self):
        p1 = AdpProcess(code="LPN-001-2026-ADP", description="x", work_id=3, documents=[])
        p2 = AdpProcess(code="LPN-002-2026-ADP", description="x", work_id=3, documents=[])
        assert p1.content_hash() != p2.content_hash()

    def test_different_docs_different_hash(self):
        doc = AdpDocument(
            title="BASES", vigencia_desde="01.01.26", vigencia_hasta="31.12.26",
            download_url="https://example.com/f.pdf",
            name_file="abc.pdf", new_name="bases.pdf",
        )
        p1 = AdpProcess(code="LPN-001-2026-ADP", description="x", work_id=3, documents=[])
        p2 = AdpProcess(code="LPN-001-2026-ADP", description="x", work_id=3, documents=[doc])
        assert p1.content_hash() != p2.content_hash()

    def test_document_order_irrelevant(self):
        d1 = AdpDocument(title="A", vigencia_desde=None, vigencia_hasta=None,
                         download_url="https://example.com/a.pdf", name_file="aaa.pdf", new_name="a.pdf")
        d2 = AdpDocument(title="B", vigencia_desde=None, vigencia_hasta=None,
                         download_url="https://example.com/b.pdf", name_file="bbb.pdf", new_name="b.pdf")
        p1 = AdpProcess(code="LPN-001-2026-ADP", description="x", work_id=3, documents=[d1, d2])
        p2 = AdpProcess(code="LPN-001-2026-ADP", description="x", work_id=3, documents=[d2, d1])
        assert p1.content_hash() == p2.content_hash()


class TestRealSampleHtml:
    """Tests con el HTML real del portal (si existe el archivo)."""

    @pytest.fixture
    def sample_html(self):
        path = Path("/tmp/adp_sample_work3.html")
        if not path.exists():
            pytest.skip("Archivo /tmp/adp_sample_work3.html no disponible")
        return path.read_text()

    def test_parse_real_html(self, sample_html):
        processes = parse_adp_html(sample_html, work_id=3)
        assert len(processes) > 0
        for proc in processes:
            assert proc.code
            assert proc.work_id == 3

    def test_all_docs_have_urls(self, sample_html):
        processes = parse_adp_html(sample_html, work_id=3)
        for proc in processes:
            for doc in proc.documents:
                assert doc.download_url.startswith("https://www.adp.com.pe/")
                assert doc.name_file.endswith(".pdf")
