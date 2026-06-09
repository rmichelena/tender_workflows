"""Búsqueda de procesos SEACE en listados paginados."""

from __future__ import annotations

import logging

import requests

from .client import FichaResult, ProcessRow, SeaceClient
from .config import AppConfig
from .db.models import FeedItem

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SEARCH_CAP = 200


def normalize_nomenclatura(value: str | None) -> str:
    return " ".join((value or "").strip().upper().split())


def client_for_entity(
    config: AppConfig,
    ruc: str,
    anio: int,
    *,
    http_session: requests.Session | None = None,
) -> SeaceClient:
    client = SeaceClient(
        ruc,
        anio,
        config.rows_per_page,
        http_proxy=config.http_proxy,
    )
    if http_session is not None:
        client.session = http_session
    return client


def client_for_process(
    config: AppConfig,
    process: FeedItem,
    *,
    http_session: requests.Session | None = None,
) -> SeaceClient:
    if not process.entity:
        raise RuntimeError(f"Proceso {process.id} no tiene entidad asociada")
    return client_for_entity(
        config,
        process.entity.ruc,
        process.anio,
        http_session=http_session,
    )


def search_list_row_by_nomenclatura(
    client: SeaceClient,
    nomenclatura: str,
    *,
    entity_label: str = "",
    page_cap: int = DEFAULT_PAGE_SEARCH_CAP,
) -> ProcessRow:
    """
    Localiza la fila vigente en el listado SEACE.

    Recorre páginas 0→N y, en cada una, filas de arriba hacia abajo; devuelve
    el primer match (el listado SEACE ordena lo más reciente arriba y en
    páginas bajas).
    """
    target = normalize_nomenclatura(nomenclatura)
    if not target:
        raise RuntimeError("Nomenclatura vacía para búsqueda en SEACE")

    _, first_soup = client.fetch_list_page(0)
    total_pages = min(client.total_pages(first_soup), page_cap)
    page_soups = {0: first_soup}

    for page in range(total_pages):
        if page not in page_soups:
            _, page_soups[page] = client.fetch_list_page(page)
        for row in client.parse_rows(page_soups[page]):
            if normalize_nomenclatura(row.nomenclatura) == target:
                return row

    scope = f" entidad {entity_label}" if entity_label else ""
    raise RuntimeError(
        f"Proceso {nomenclatura} no aparece en las {total_pages} página(s) actuales{scope}"
    )


def resolve_process_row(
    config: AppConfig,
    process: FeedItem,
    client: SeaceClient | None = None,
) -> tuple[ProcessRow, SeaceClient]:
    """Resuelve la fila de listado SEACE para un FeedItem almacenado."""
    if client is None:
        client = client_for_process(config, process)
    row = search_list_row_by_nomenclatura(
        client,
        process.nomenclatura,
        entity_label=str(process.entity_id),
    )
    return row, client


def open_ficha_for_process(
    config: AppConfig,
    process: FeedItem,
    *,
    client: SeaceClient | None = None,
    http_session: requests.Session | None = None,
) -> tuple[ProcessRow, FichaResult, SeaceClient]:
    """Busca por nomenclatura y abre la ficha con ViewState coherente."""
    if client is None:
        client = client_for_process(config, process, http_session=http_session)
    elif http_session is not None:
        client.session = http_session
    row = search_list_row_by_nomenclatura(
        client,
        process.nomenclatura,
        entity_label=str(process.entity_id),
    )
    return row, client.open_ficha(row), client


def apply_list_row_to_process(process: FeedItem, row: ProcessRow) -> None:
    process.nid_proceso = row.nid_proceso
    process.link_id = row.link_id
    process.nid_convocatoria = row.nid_convocatoria
    process.nid_sistema = row.nid_sistema
    process.ntipo = row.ntipo
    if row.fecha_publicacion:
        process.fecha_publicacion = row.fecha_publicacion


def log_resolved_row_change(process: FeedItem, row: ProcessRow, *, context: str) -> None:
    if row.link_id == (process.link_id or "") and row.nid_proceso == process.nid_proceso:
        return
    logger.info(
        "%s: fila resuelta id=%s nid %s → %s link_id %s → %s",
        context,
        process.id,
        process.nid_proceso,
        row.nid_proceso,
        process.link_id,
        row.link_id,
    )
