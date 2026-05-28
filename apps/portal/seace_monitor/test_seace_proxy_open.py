"""Tests del proxy para abrir fichas SEACE desde la UI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from .client import FichaResult, ProcessRow
from .config import AppConfig
from .db.models import Entity, Process
from .web.seace_proxy import _try_server_open_ficha


def test_seace_open_uses_open_ficha_for_process_after_pagination():
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
    ficha_result = FichaResult(
        ficha_id="abc",
        html="<html></html>",
        url="https://prod2.seace.gob.pe/seacebus-uiwd-pub/fichaSeleccion/fichaSeleccion.xhtml?id=abc",
    )
    session = MagicMock()

    with patch(
        "seace_monitor.web.seace_proxy.open_ficha_for_process",
        return_value=(current_row, ficha_result, MagicMock()),
    ) as open_ficha:
        location = _try_server_open_ficha(
            session,
            process,
            "<html></html>",
            "https://prod2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/ongei/buscadorPublico.xhtml",
            AppConfig(),
        )

    assert location == "/seace/p/fichaSeleccion/fichaSeleccion.xhtml?id=abc"
    open_ficha.assert_called_once_with(AppConfig(), process, http_session=session)
    session.post.assert_not_called()
