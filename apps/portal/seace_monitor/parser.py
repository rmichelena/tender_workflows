"""Parseo de HTML de ficha y listado."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from .client import ProcessRow


@dataclass
class CronogramaEtapa:
    etapa: str
    fecha_inicio: str
    fecha_fin: str


@dataclass
class Documento:
    uuid: str
    nombre: str
    etapa: str
    tipo_documento: str
    tamano_kb: str
    fecha_publicacion: str
    tipo_descarga: str  # 3 = privado Alfresco


@dataclass
class FichaData:
    ficha_id: str
    nid_proceso: str
    nomenclatura: str
    descripcion: str
    objeto: str
    fecha_publicacion: str
    cronograma: list[CronogramaEtapa] = field(default_factory=list)
    documentos: list[Documento] = field(default_factory=list)
    raw_labels: dict[str, str] = field(default_factory=dict)

    def content_hash(self) -> str:
        payload = {
            "cronograma": [asdict(c) for c in self.cronograma],
            "documentos": [asdict(d) for d in self.documentos],
            "fecha_publicacion": self.fecha_publicacion,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


def row_snapshot_hash(row: ProcessRow) -> str:
    payload = {
        "fecha_publicacion": row.fecha_publicacion,
        "nomenclatura": row.nomenclatura,
        "descripcion": row.descripcion,
        "cuantia": row.cuantia,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def parse_ficha(html: str, ficha_id: str, nid_proceso: str) -> FichaData:
    soup = BeautifulSoup(html, "lxml")
    data = FichaData(
        ficha_id=ficha_id,
        nid_proceso=nid_proceso,
        nomenclatura="",
        descripcion="",
        objeto="",
        fecha_publicacion="",
    )

    data.nomenclatura = _label_value(soup, "Nomenclatura:")
    data.objeto = _label_value(soup, "Objeto de Contratación:")
    data.descripcion = _label_value(soup, "Descripción del Objeto:")
    data.fecha_publicacion = _label_value(soup, "Fecha y Hora Publicación:")

    data.cronograma = _parse_cronograma(soup)
    data.documentos = _parse_documentos(soup)
    return data


def _label_value(soup: BeautifulSoup, label: str) -> str:
    node = soup.find(string=re.compile(re.escape(label)))
    if not node:
        return ""
    row = node.find_parent("tr")
    if not row:
        return ""
    cells = row.find_all("td")
    texts = [c.get_text(strip=True) for c in cells]
    for i, t in enumerate(texts):
        if label.rstrip(":") in t and i + 1 < len(texts):
            return texts[i + 1]
    # fallback: texto después del label en la misma fila
    full = row.get_text(" ", strip=True)
    if ":" in full:
        parts = full.split(":", 1)
        if len(parts) > 1:
            return parts[1].strip()
    return ""


_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}")


def _parse_cronograma(soup: BeautifulSoup) -> list[CronogramaEtapa]:
    """Extrae etapas del cronograma (tabla anidada bajo el panel Cronograma)."""
    # Tabla específica del cronograma en SEACE 3.0 (evita el contenedor general)
    table = soup.find("table", id=re.compile(r"tbFicha:j_idt\d+$"))
    candidates = [table] if table else []
    candidates.extend(soup.find_all("table"))

    for table in candidates:
        if table is None:
            continue
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if headers != ["Etapa", "Fecha Inicio", "Fecha Fin"]:
            continue

        etapas: list[CronogramaEtapa] = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) != 3:
                continue
            etapa, inicio, fin = cells
            if not _DATE_RE.match(inicio):
                continue
            etapas.append(
                CronogramaEtapa(etapa=etapa, fecha_inicio=inicio, fecha_fin=fin)
            )
        if etapas:
            return etapas
    return []


@dataclass
class CronogramaFechasClave:
    """Fechas derivadas del cronograma para la tabla de publicaciones."""

    fecha_consultas: str = ""
    fecha_presentacion: str = ""


def extract_cronograma_fechas(cronograma: list[CronogramaEtapa]) -> CronogramaFechasClave:
    """
    Mapea etapas del cronograma SEACE a columnas de la UI.
    - fecha_consultas: etapa de consultas / absolución (fin de consultas)
    - fecha_presentacion: inicio de presentación de propuestas
    """
    consultas = ""
    presentacion = ""

    for etapa in cronograma:
        nombre = etapa.etapa.lower()
        if any(k in nombre for k in ("consulta", "absolución", "absolucion", "aclaracion", "aclaración")):
            consultas = etapa.fecha_fin or etapa.fecha_inicio
        if any(k in nombre for k in ("presentación", "presentacion", "propuesta")):
            if not presentacion:
                presentacion = etapa.fecha_inicio

    return CronogramaFechasClave(
        fecha_consultas=consultas,
        fecha_presentacion=presentacion,
    )


def _parse_documentos(soup: BeautifulSoup) -> list[Documento]:
    docs: list[Documento] = []
    for a in soup.find_all("a", onclick=re.compile(r"descargaDocGeneral")):
        m = re.search(
            r"descargaDocGeneral\('([^']+)','(\d+)','([^']*)'\)",
            a.get("onclick", ""),
        )
        if not m:
            continue
        uuid, tipo, nombre = m.groups()
        tr = a.find_parent("tr")
        etapa, tipo_doc, fecha = "", "", ""
        if tr:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            # Nro | Etapa | Documento | Archivo | Fecha | Acciones
            if len(cells) >= 5:
                etapa = cells[1] if len(cells) > 1 else ""
                tipo_doc = cells[2] if len(cells) > 2 else ""
                fecha = cells[4] if len(cells) > 4 else ""
        tamano = a.get_text(strip=True).strip("()")
        docs.append(
            Documento(
                uuid=uuid,
                nombre=nombre,
                etapa=etapa,
                tipo_documento=tipo_doc,
                tamano_kb=tamano.replace(" KB", ""),
                fecha_publicacion=fecha,
                tipo_descarga=tipo,
            )
        )
    return docs
