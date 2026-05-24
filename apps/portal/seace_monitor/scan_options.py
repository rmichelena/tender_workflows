"""Opciones de escaneo acotado (entidades / fechas / paginación)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")


class ScanDateMode(str, Enum):
    future_presentacion = "future_presentacion"
    current_year = "current_year"
    since_date = "since_date"


class RemovedEntityPolicy(str, Enum):
    keep_all = "keep_all"
    keep_analyzed = "keep_analyzed"
    discard_all = "discard_all"


@dataclass(frozen=True)
class ScanOptions:
    entity_ids: frozenset[int] | None = None
    date_mode: ScanDateMode | None = None
    since_date: date | None = None
    multipage: bool = False
    max_pages_cap: int = 200


def passes_date_filter(
    fecha_presentacion: str | None,
    *,
    fecha_publicacion: str | None = None,
    mode: ScanDateMode | None,
    since_date: date | None = None,
    now: datetime | None = None,
) -> bool:
    if mode is None:
        return True
    now = now or datetime.now(LIMA)
    today = now.date()
    current_year = now.year
    presentacion = parse_seace_date(fecha_presentacion)
    publicacion = parse_seace_date(fecha_publicacion)

    if mode == ScanDateMode.future_presentacion:
        if presentacion is None:
            return False
        return presentacion.date() > today

    if mode == ScanDateMode.current_year:
        if presentacion is not None:
            return presentacion.year == current_year
        if publicacion is not None:
            return publicacion.year == current_year
        return False

    if mode == ScanDateMode.since_date:
        if since_date is None:
            return True
        if presentacion is not None:
            return presentacion.date() >= since_date
        if publicacion is not None:
            return publicacion.date() >= since_date
        return False

    return True


def parse_seace_date(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in (
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=LIMA)
        except ValueError:
            continue
        if dt.year < 2000 or dt.year > datetime.now(LIMA).year + 2:
            return None
        return dt
    return None


def parse_ddmmyy(value: str) -> date | None:
    text = value.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def default_since_date(now: datetime | None = None) -> date:
    now = now or datetime.now(LIMA)
    year = now.year - 1
    month = now.month
    day = now.day
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)
