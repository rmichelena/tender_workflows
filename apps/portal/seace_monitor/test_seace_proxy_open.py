"""Tests del proxy para abrir fichas SEACE desde la UI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from .client import ProcessRow
from .config import AppConfig
from .db.models import Entity, Process
from .web.seace_proxy import _try_server_open_ficha


class _Response:
    url = "https://prod2.seace.gob.pe/seacebus-uiwd-pub/fichaSeleccion/fichaSeleccion.xhtml?id=abc"


class _Session:
    def __init__(self) -> None:
        self.post_data: dict[str, str] | None = None

    def post(self, _url, data, headers, timeout, allow_redirects):
        self.post_data = data
        return _Response()


def test_seace_open_resolves_row_by_nomenclatura_across_pages():
    entity = Entity(ruc="20122476309", nombre="BCR", activa=True)
    process = Process(
        id=29,
        entity=entity,
        entity_id=1,
        anio=2026,
        nid_proceso="old-nid",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
        nid_convocatoria="old-conv",
        link_id="old-link",
    )
    current_row = ProcessRow(
        row_index=18,
        numero="19",
        fecha_publicacion="06/05/2026 16:35",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
        reiniciado_desde="",
        objeto="Bien",
        descripcion="Adquisición e instalación de sistema inalámbrico de conferencia",
        cuantia="---",
        moneda="Soles",
        version_seace="3",
        nid_proceso="fresh-nid",
        nid_convocatoria="fresh-conv",
        nid_sistema="3",
        link_id="fresh-link",
        ntipo="0",
    )
    list_html = """
    <html><body>
      <form id="formBuscador" action="/buscadorPublico.xhtml">
        <input type="hidden" name="javax.faces.ViewState" value="vs1" />
      </form>
      <table><tbody id="formBuscador:dtProcesos_data"></tbody></table>
    </body></html>
    """
    session = _Session()

    with (
        patch("seace_monitor.web.seace_proxy.SeaceClient", return_value=MagicMock()),
        patch("seace_monitor.web.seace_proxy._resolve_current_row", return_value=current_row),
    ):
        location = _try_server_open_ficha(
            session,
            process,
            list_html,
            "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/ongei/buscadorPublico.xhtml?ruc_entidad=20122476309&anio=2026",
            AppConfig(),
        )

    assert location == "/seace/p/fichaSeleccion/fichaSeleccion.xhtml?id=abc"
    assert session.post_data is not None
    assert session.post_data["fresh-link"] == "fresh-link"
    assert session.post_data["nidProceso"] == "fresh-nid"
    assert session.post_data["nidConvocatoria"] == "fresh-conv"
