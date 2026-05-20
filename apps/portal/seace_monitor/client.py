"""Cliente HTTP para SEACE (JSF/PrimeFaces)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://prod2.seace.gob.pe/seacebus-uiwd-pub"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class ProcessRow:
    """Fila del buscador público ONGEI."""

    row_index: int
    numero: str
    fecha_publicacion: str
    nomenclatura: str
    reiniciado_desde: str
    objeto: str
    descripcion: str
    cuantia: str
    moneda: str
    version_seace: str
    nid_proceso: str
    nid_convocatoria: str
    nid_sistema: str
    link_id: str
    ntipo: str

    @property
    def key(self) -> str:
        return self.nid_proceso


@dataclass
class FichaResult:
    ficha_id: str
    html: str
    url: str


class SeaceClient:
    def __init__(self, ruc_entidad: str, anio: int, rows_per_page: int = 15) -> None:
        self.ruc_entidad = ruc_entidad
        self.anio = anio
        self.rows_per_page = rows_per_page
        self.list_url = (
            f"{BASE_URL}/buscadorPublico/ongei/buscadorPublico.xhtml"
            f"?ruc_entidad={ruc_entidad}&anio={anio}"
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _view_state(self, soup: BeautifulSoup) -> str:
        el = soup.find("input", {"name": "javax.faces.ViewState"})
        if not el or not el.get("value"):
            raise RuntimeError("No se encontró javax.faces.ViewState en la respuesta")
        return el["value"]

    def _form_action(self, soup: BeautifulSoup) -> str:
        form = soup.find("form", id="formBuscador")
        if not form:
            raise RuntimeError("No se encontró formBuscador")
        return urljoin(self.list_url, form["action"])

    def fetch_list_page(self, page_index: int = 0) -> tuple[str, BeautifulSoup]:
        """Obtiene una página del listado (page_index base 0)."""
        r = self.session.get(self.list_url, timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        if page_index > 0:
            action = self._form_action(soup)
            vs = self._view_state(soup)
            data: dict[str, str] = {
                "formBuscador": "formBuscador",
                "javax.faces.ViewState": vs,
                "formBuscador:dtProcesos": "formBuscador:dtProcesos",
                "formBuscador:dtProcesos_pagination": "true",
                "formBuscador:dtProcesos_first": str(page_index * self.rows_per_page),
                "formBuscador:dtProcesos_rows": str(self.rows_per_page),
                "formBuscador:dtProcesos_page": str(page_index),
                "formBuscador:dtProcesos_skipChildren": "true",
            }
            r = self.session.post(action, data=data, timeout=60)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

        return r.text, soup

    def parse_rows(self, soup: BeautifulSoup) -> list[ProcessRow]:
        tbody = soup.find("tbody", id="formBuscador:dtProcesos_data")
        if not tbody:
            return []

        rows: list[ProcessRow] = []
        for tr in tbody.find_all("tr", recursive=False):
            ri = int(tr.get("data-ri", len(rows)))
            cells = tr.find_all("td", recursive=False)
            if len(cells) < 10:
                continue

            link = tr.find("a", onclick=re.compile(r"nidConvocatoria"))
            if not link:
                continue

            params = dict(re.findall(r"'([^']+)':'([^']*)'", link.get("onclick", "")))
            link_id = link.get("id", "")
            for k, v in params.items():
                if k.startswith("formBuscador:dtProcesos") and k == v:
                    link_id = k
                    break

            rows.append(
                ProcessRow(
                    row_index=ri,
                    numero=_cell_text(cells[0]),
                    fecha_publicacion=_cell_text(cells[1]),
                    nomenclatura=_cell_text(cells[2]),
                    reiniciado_desde=_cell_text(cells[3]),
                    objeto=_cell_text(cells[4]),
                    descripcion=_cell_text(cells[5]),
                    cuantia=_cell_text(cells[6]),
                    moneda=_cell_text(cells[7]),
                    version_seace=_cell_text(cells[8]),
                    nid_proceso=params.get("nidProceso", ""),
                    nid_convocatoria=params.get("nidConvocatoria", ""),
                    nid_sistema=params.get("nidSistema", "3"),
                    link_id=link_id,
                    ntipo=params.get("ntipo", "0"),
                )
            )

        return rows

    def total_pages(self, soup: BeautifulSoup) -> int:
        paginator = soup.find("div", id=re.compile("dtProcesos_paginator"))
        if not paginator:
            return 1
        script = soup.find("script", id="formBuscador:dtProcesos_s")
        if script and script.string:
            m = re.search(r"totalPages:(\d+)", script.string)
            if m:
                return int(m.group(1))
        text = paginator.get_text()
        m = re.search(r"Página:\s*\d+/(\d+)", text)
        return int(m.group(1)) if m else 1

    def open_ficha(self, row: ProcessRow) -> FichaResult:
        """
        Abre la ficha de selección mediante POST JSF (como el ícono del calendario).
        Debe llamarse en la misma sesión que obtuvo el listado.
        """
        r = self.session.get(self.list_url, timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        action = self._form_action(soup)
        vs = self._view_state(soup)

        post_data: dict[str, str] = {
            "formBuscador": "formBuscador",
            "javax.faces.ViewState": vs,
            "ntipo": row.ntipo,
            row.link_id: row.link_id,
            "nidConvocatoria": row.nid_convocatoria,
            "nidProceso": row.nid_proceso,
            "nidSistema": row.nid_sistema,
            "ptoRetorno": "LOCAL_ONGEI",
        }

        r2 = self.session.post(action, data=post_data, timeout=60, allow_redirects=True)
        r2.raise_for_status()

        m = re.search(
            r"id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            r2.url,
            re.I,
        )
        if not m:
            hidden = BeautifulSoup(r2.text, "lxml").find(
                "input", {"name": re.compile("hiddenId", re.I)}
            )
            ficha_id = hidden["value"] if hidden and hidden.get("value") else ""
        else:
            ficha_id = m.group(1)

        if not ficha_id:
            raise RuntimeError(f"No se obtuvo ID de ficha para proceso {row.nid_proceso}")

        return FichaResult(ficha_id=ficha_id, html=r2.text, url=r2.url)


def _cell_text(cell: Any) -> str:
    return cell.get_text(strip=True)
