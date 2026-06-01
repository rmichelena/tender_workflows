"""Parser HTML del portal de Aeropuertos del Perú (ADP).

Extrae procesos de selección y sus documentos desde el HTML parcial
que devuelve el endpoint ``get_partial_view_competition``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, parse_qs, unquote

from bs4 import BeautifulSoup, Tag

ADP_BASE_URL = "https://www.adp.com.pe"

_RE_VIGENCIA = re.compile(
    r"Fecha de vigencia:\s*Desde\s+(\d{2}\.\d{2}\.\d{2})\s+Hasta\s+(\d{2}\.\d{2}\.\d{2})",
    re.IGNORECASE | re.DOTALL,
)


# ── Data classes ──────────────────────────────────────────────


@dataclass(frozen=True)
class AdpDocument:
    """Documento adjunto a un proceso ADP."""

    title: str
    vigencia_desde: str | None  # DD.MM.YY
    vigencia_hasta: str | None  # DD.MM.YY
    download_url: str  # URL absoluta
    name_file: str  # nombre hash en el servidor
    new_name: str  # nombre amigable


@dataclass
class AdpProcess:
    """Proceso de selección del portal ADP."""

    code: str  # ej. "LPN-003-2026-ADP"
    description: str
    work_id: int  # 1=consultorías, 2=obras, 3=bienes, 4=servicios
    documents: list[AdpDocument] = field(default_factory=list)

    def content_hash(self) -> str:
        """Hash SHA-256 para detección de cambios."""
        payload = _fingerprint_payload(self)
        return hashlib.sha256(payload.encode()).hexdigest()


# ── Parsing ───────────────────────────────────────────────────


def parse_vigencia(text: str | None) -> tuple[str | None, str | None]:
    """Extrae fechas desde/hasta desde texto de vigencia.

    Returns:
        Tupla (desde, hasta) en formato ``DD.MM.YY``, o ``(None, None)``.
    """
    if not text:
        return None, None
    m = _RE_VIGENCIA.search(text)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _parse_download_link(a_tag: Tag) -> tuple[str, str, str] | None:
    """Extrae URL, name_file y new_name de un tag ``<a>`` de descarga."""
    href = a_tag.get("href", "").strip()
    if not href:
        return None
    url = urljoin(ADP_BASE_URL + "/", href)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    name_file_list = qs.get("name_file")
    new_name_list = qs.get("new_name")
    if not name_file_list:
        return None
    name_file = name_file_list[0]
    new_name = unquote(new_name_list[0]) if new_name_list else name_file
    return url, name_file, new_name


def _parse_document_entry(div: Tag) -> AdpDocument | None:
    """Parsea un ``div`` de documento individual dentro de un proceso."""
    a_tag = div.select_one("a[href*=getFile_store_competition_download]")
    if not a_tag:
        return None
    parsed = _parse_download_link(a_tag)
    if not parsed:
        return None
    url, name_file, new_name = parsed

    text_container = div.select_one(".container-text-pdf-title")
    title = ""
    vigencia_text = ""
    if text_container:
        title_span = text_container.select_one("span.title")
        if title_span:
            title = title_span.get_text(strip=True)
        excerpt_span = text_container.select_one("span.excerpt")
        if excerpt_span:
            vigencia_text = excerpt_span.get_text(strip=True)

    vigencia_desde, vigencia_hasta = parse_vigencia(vigencia_text)
    return AdpDocument(
        title=title,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
        download_url=url,
        name_file=name_file,
        new_name=new_name,
    )


def parse_adp_html(html: str, work_id: int) -> list[AdpProcess]:
    """Parsea el HTML completo de una categoría ADP.

    Args:
        html: HTML parcial del endpoint ``get_partial_view_competition``.
        work_id: Identificador de categoría (1-4).

    Returns:
        Lista de :class:`AdpProcess` encontrados.
    """
    soup = BeautifulSoup(html, "html.parser")
    processes: list[AdpProcess] = []

    for li in soup.select("li.parent-main-competition"):
        # ── Cabecera del proceso ──
        title_div = li.select_one(".container-title-competition")
        if not title_div:
            continue
        text_container = title_div.select_one(".container-text-pdf-title")
        if not text_container:
            continue

        code_span = text_container.select_one("span.title")
        excerpt_span = text_container.select_one("span.excerpt")
        if not code_span:
            continue

        code = code_span.get_text(strip=True)
        description = excerpt_span.get_text(strip=True) if excerpt_span else ""

        # ── Documentos ──
        docs: list[AdpDocument] = []
        container = li.select_one(".container-competition")
        if container:
            for doc_div in container.select(
                "div.container-agreements.container-participations-item"
            ):
                doc = _parse_document_entry(doc_div)
                if doc:
                    docs.append(doc)

        processes.append(
            AdpProcess(
                code=code,
                description=description,
                work_id=work_id,
                documents=docs,
            )
        )

    return processes


def _fingerprint_payload(process: AdpProcess) -> str:
    """Genera carga útil determinista para el hash de cambios."""
    parts: list[str] = [process.code, str(process.work_id)]
    for doc in sorted(process.documents, key=lambda d: d.name_file):
        parts.append(f"{doc.name_file}|{doc.title}|{doc.vigencia_desde}|{doc.vigencia_hasta}")
    return "|".join(parts)
