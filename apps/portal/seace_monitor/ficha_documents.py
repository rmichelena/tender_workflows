"""Paginación JSF de la tabla de documentos en ficha SEACE."""

from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from .parser import Documento, _parse_documentos

logger = logging.getLogger(__name__)

FICHA_DOC_TABLE_ID = "tbFicha:dtDocumentos"


def collect_ficha_documentos(
    html: str,
    session: requests.Session,
    ficha_url: str,
) -> list[Documento]:
    """Parsea documentos de todas las páginas de la tabla dtDocumentos."""
    soup = BeautifulSoup(html, "lxml")
    docs = _parse_documentos(soup)
    total_pages, rows_per_page = _ficha_documentos_pagination(soup)
    if total_pages <= 1:
        return docs

    view_state = _view_state(soup)
    seen = {doc.uuid for doc in docs}
    for page_index in range(1, total_pages):
        page_soup = _fetch_documentos_page(
            session,
            ficha_url,
            view_state,
            page_index=page_index,
            rows_per_page=rows_per_page,
        )
        for doc in _parse_documentos(page_soup):
            if doc.uuid in seen:
                continue
            seen.add(doc.uuid)
            docs.append(doc)
    return docs


def _ficha_documentos_pagination(soup: BeautifulSoup) -> tuple[int, int]:
    """Devuelve (total_páginas, filas_por_página)."""
    for script in soup.find_all("script"):
        text = script.string or ""
        if "dtDocumentos" not in text or "paginator" not in text:
            continue
        match = re.search(
            r'id:"tbFicha:dtDocumentos".*?rows:(\d+).*?rowCount:(\d+)',
            text,
            re.DOTALL,
        )
        if match:
            rows_per_page = int(match.group(1))
            row_count = int(match.group(2))
            if row_count <= rows_per_page:
                return 1, rows_per_page
            return (row_count + rows_per_page - 1) // rows_per_page, rows_per_page

    paginator = soup.find(id=f"{FICHA_DOC_TABLE_ID}_paginator_bottom")
    if paginator is not None:
        current = paginator.find(class_="ui-paginator-current")
        if current:
            match = re.search(r"(\d+)\s+de\s+(\d+)", current.get_text())
            if match:
                total_pages = int(match.group(2))
                rows_per_page = 5
                select = paginator.find("select", class_="ui-paginator-rpp-options")
                if select is not None:
                    option = select.find("option", selected=True)
                    if option is not None and option.get("value"):
                        rows_per_page = int(option["value"])
                return total_pages, rows_per_page

    return 1, 5


def _view_state(soup: BeautifulSoup) -> str:
    el = soup.find("input", {"name": "javax.faces.ViewState"})
    if not el or not el.get("value"):
        raise RuntimeError("No se encontró javax.faces.ViewState en la ficha")
    return el["value"]


def _fetch_documentos_page(
    session: requests.Session,
    ficha_url: str,
    view_state: str,
    *,
    page_index: int,
    rows_per_page: int,
) -> BeautifulSoup:
    data = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": FICHA_DOC_TABLE_ID,
        "javax.faces.partial.execute": FICHA_DOC_TABLE_ID,
        "javax.faces.partial.render": FICHA_DOC_TABLE_ID,
        "javax.faces.behavior.event": "page",
        "javax.faces.partial.event": "page",
        "tbFicha": "tbFicha",
        "javax.faces.ViewState": view_state,
        f"{FICHA_DOC_TABLE_ID}_pagination": "true",
        f"{FICHA_DOC_TABLE_ID}_first": str(page_index * rows_per_page),
        f"{FICHA_DOC_TABLE_ID}_rows": str(rows_per_page),
        f"{FICHA_DOC_TABLE_ID}_encodeFeature": "true",
    }
    response = session.post(ficha_url, data=data, timeout=60)
    response.raise_for_status()
    page_soup = _partial_documentos_soup(response.text)
    if page_soup is None:
        raise RuntimeError(
            f"No se pudo parsear página {page_index + 1} de documentos en ficha SEACE"
        )
    return page_soup


def _partial_documentos_soup(partial_xml: str) -> BeautifulSoup | None:
    xml = BeautifulSoup(partial_xml, "xml")
    update = xml.find("update", {"id": FICHA_DOC_TABLE_ID})
    if update is None:
        return None
    rows_html = update.string or update.get_text()
    return BeautifulSoup(
        f"<html><body><table><tbody>{rows_html}</tbody></table></body></html>",
        "lxml",
    )
