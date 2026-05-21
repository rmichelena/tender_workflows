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
_ETAPA_NOISE_RE = (
    re.compile(r"\s*A TRAV[EÉ]S DEL SEACE\s*", re.I),
    re.compile(r"\s*\(Electr[oó]nica\)\s*", re.I),
)


def clean_cronograma_etapa(etapa: str) -> str:
    text = etapa.strip()
    for pattern in _ETAPA_NOISE_RE:
        text = pattern.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_cronograma(soup: BeautifulSoup) -> list[CronogramaEtapa]:
    """Extrae etapas del cronograma (tabla anidada bajo el panel Cronograma)."""
    table = soup.find("table", id=re.compile(r"tbFicha:j_idt\d+$"))
    if table is not None:
        etapas = _extract_cronograma_from_table(table)
        if etapas:
            return etapas

    best: list[CronogramaEtapa] = []
    for candidate in soup.find_all("table"):
        etapas = _extract_cronograma_from_table(candidate)
        if len(etapas) >= 2 and len(etapas) > len(best):
            best = etapas
    return best


def _extract_cronograma_from_table(table) -> list[CronogramaEtapa]:
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    if headers != ["Etapa", "Fecha Inicio", "Fecha Fin"]:
        return []

    etapas: list[CronogramaEtapa] = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) != 3:
            continue
        etapa, inicio, fin = cells
        if not _DATE_RE.match(inicio):
            continue
        etapas.append(
            CronogramaEtapa(
                etapa=clean_cronograma_etapa(etapa),
                fecha_inicio=inicio,
                fecha_fin=fin,
            )
        )
    return etapas


@dataclass
class CronogramaFechasClave:
    """Fechas derivadas del cronograma para la tabla de publicaciones."""

    fecha_consultas: str = ""
    fecha_presentacion: str = ""


def extract_cronograma_fechas(cronograma: list[CronogramaEtapa]) -> CronogramaFechasClave:
    """
    Mapea etapas del cronograma SEACE a columnas de la UI.
    - fecha_consultas: fin de presentación de consultas (no absolución)
    - fecha_presentacion: fin de presentación de propuestas
    """
    best_consultas: tuple[int, str] = (-1, "")
    presentacion = ""

    for etapa in cronograma:
        nombre = etapa.etapa.lower()
        score = _score_consultas_stage(nombre)
        if score > best_consultas[0]:
            fin = _pick_fecha_fin(etapa)
            if fin:
                best_consultas = (score, fin)
        if _is_presentacion_propuestas(nombre) and not presentacion:
            presentacion = _pick_fecha_fin(etapa)

    return CronogramaFechasClave(
        fecha_consultas=best_consultas[1] if best_consultas[0] >= 0 else "",
        fecha_presentacion=presentacion,
    )


def fechas_listado_from_cronograma_json(
    cronograma_json: str | None,
    *,
    fallback_consultas: str = "",
    fallback_presentacion: str = "",
) -> CronogramaFechasClave:
    """Fechas de fin para columnas de listado, recalculadas desde cronograma_json."""
    if not cronograma_json:
        return CronogramaFechasClave(
            fecha_consultas=fallback_consultas,
            fecha_presentacion=fallback_presentacion,
        )
    try:
        raw = json.loads(cronograma_json)
    except json.JSONDecodeError:
        raw = []
    if not isinstance(raw, list):
        raw = []
    cronograma = [
        CronogramaEtapa(
            etapa=str(item.get("etapa", "")),
            fecha_inicio=str(item.get("fecha_inicio", "")),
            fecha_fin=str(item.get("fecha_fin", "")),
        )
        for item in raw
        if isinstance(item, dict)
    ]
    if not cronograma:
        return CronogramaFechasClave(
            fecha_consultas=fallback_consultas,
            fecha_presentacion=fallback_presentacion,
        )
    return extract_cronograma_fechas(cronograma)


def _pick_fecha_fin(etapa: CronogramaEtapa) -> str:
    return etapa.fecha_fin or etapa.fecha_inicio


def _score_consultas_stage(nombre: str) -> int:
    if _is_absolucion(nombre):
        return -1
    if _is_presentacion_consultas(nombre):
        return 100
    if _is_consultas_stage(nombre):
        return 50
    return -1


def _is_absolucion(nombre: str) -> bool:
    return any(k in nombre for k in ("absolución", "absolucion"))


def _is_presentacion_consultas(nombre: str) -> bool:
    if "consulta" not in nombre:
        return False
    return any(k in nombre for k in ("presentación", "presentacion", "registro"))


def _is_consultas_stage(nombre: str) -> bool:
    return any(k in nombre for k in ("consulta", "aclaracion", "aclaración"))


def _is_presentacion_propuestas(nombre: str) -> bool:
    if "consulta" in nombre:
        return False
    return any(k in nombre for k in ("presentación", "presentacion", "propuesta"))


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
