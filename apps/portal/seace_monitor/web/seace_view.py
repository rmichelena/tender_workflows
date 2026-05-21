"""Helpers para abrir fichas SEACE desde la UI."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..client import ProcessRow
from ..db.models import Process


def can_open_seace(process: Process) -> bool:
    return bool(
        process.nid_proceso
        and process.nid_convocatoria
        and process.link_id
        and process.entity
    )


def process_row_from_model(process: Process) -> ProcessRow:
    return ProcessRow(
        row_index=0,
        numero=process.numero or "",
        fecha_publicacion=process.fecha_publicacion or "",
        nomenclatura=process.nomenclatura,
        reiniciado_desde=process.reiniciado_desde or "",
        objeto=process.objeto or "",
        descripcion=process.descripcion or "",
        cuantia=process.cuantia or "",
        moneda=process.moneda or "",
        version_seace=process.version_seace or "",
        nid_proceso=process.nid_proceso,
        nid_convocatoria=process.nid_convocatoria or "",
        nid_sistema=process.nid_sistema or "3",
        link_id=process.link_id or "",
        ntipo=process.ntipo or "0",
    )


def row_from_list_html(html: str, nid_proceso: str) -> ProcessRow | None:
    """Localiza la fila actual del listado SEACE (link_id JSF cambia con el orden)."""
    soup = BeautifulSoup(html, "lxml")
    tbody = soup.find("tbody", id="formBuscador:dtProcesos_data")
    if not tbody:
        return None

    for tr in tbody.find_all("tr", recursive=False):
        link = tr.find("a", onclick=re.compile(r"nidConvocatoria"))
        if not link:
            continue
        params = dict(re.findall(r"'([^']+)':'([^']*)'", link.get("onclick", "")))
        if params.get("nidProceso") != nid_proceso:
            continue

        link_id = link.get("id", "")
        for key, value in params.items():
            if key.startswith("formBuscador:dtProcesos") and key == value:
                link_id = key
                break

        cells = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
        return ProcessRow(
            row_index=int(tr.get("data-ri", 0)),
            numero=cells[0] if cells else "",
            fecha_publicacion=cells[1] if len(cells) > 1 else "",
            nomenclatura=cells[2] if len(cells) > 2 else "",
            reiniciado_desde=cells[3] if len(cells) > 3 else "",
            objeto=cells[4] if len(cells) > 4 else "",
            descripcion=cells[5] if len(cells) > 5 else "",
            cuantia=cells[6] if len(cells) > 6 else "",
            moneda=cells[7] if len(cells) > 7 else "",
            version_seace=cells[8] if len(cells) > 8 else "",
            nid_proceso=params.get("nidProceso", ""),
            nid_convocatoria=params.get("nidConvocatoria", ""),
            nid_sistema=params.get("nidSistema", "3"),
            link_id=link_id,
            ntipo=params.get("ntipo", "0"),
        )
    return None
