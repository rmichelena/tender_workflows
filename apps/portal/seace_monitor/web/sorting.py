"""Ordenamiento de procesos en la UI."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlencode

from ..db.models import Process

_DATE_TIME_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?"
)

SORTABLE_COLUMNS: dict[str, str] = {
    "numero": "N°",
    "entidad": "Entidad",
    "fecha_publicacion": "Fecha pub.",
    "nomenclatura": "Nomenclatura",
    "objeto": "Objeto",
    "descripcion": "Descripción",
    "cuantia": "Cuantía",
    "moneda": "Moneda",
    "fecha_consultas": "Fin consultas",
    "fecha_presentacion": "Fin presentación",
    "estado": "Estado",
}

DEFAULT_SORT = "fecha_publicacion"
DEFAULT_DIR = "desc"
DATE_COLUMNS = frozenset(
    {"fecha_publicacion", "fecha_consultas", "fecha_presentacion"}
)


def normalize_sort(sort: str | None) -> str:
    if sort and sort in SORTABLE_COLUMNS:
        return sort
    return DEFAULT_SORT


def normalize_dir(direction: str | None, sort: str | None = None) -> str:
    if direction in ("asc", "desc"):
        return direction
    if sort in DATE_COLUMNS:
        return "desc"
    return "asc"


def toggle_dir(sort: str, current_sort: str, current_dir: str) -> str:
    if sort == current_sort:
        return "desc" if current_dir == "asc" else "asc"
    return "desc" if sort in DATE_COLUMNS else "asc"


def parse_seace_datetime(value: str | None) -> float:
    if not value:
        return 0.0
    m = _DATE_TIME_RE.match(value.strip())
    if not m:
        return 0.0
    d, mo, y, h, mi, s = m.groups()
    try:
        dt = datetime(
            int(y),
            int(mo),
            int(d),
            int(h or 0),
            int(mi or 0),
            int(s or 0),
        )
        return dt.timestamp()
    except ValueError:
        return 0.0


def _int_or_zero(value: str | None) -> int:
    if not value:
        return 0
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else 0


def _float_or_zero(value: str | None) -> float:
    if not value:
        return 0.0
    cleaned = value.strip().replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _text(value: str | None) -> str:
    return (value or "").lower()


def sort_key(column: str) -> Callable[[Process], tuple[Any, ...]]:
    if column == "numero":
        return lambda p: (_int_or_zero(p.numero), _text(p.numero))
    if column == "entidad":
        return lambda p: (_text(p.entity.nombre if p.entity else ""),)
    if column in DATE_COLUMNS:
        return lambda p, c=column: (
            parse_seace_datetime(getattr(p, c)),
            _text(getattr(p, c)),
        )
    if column == "cuantia":
        return lambda p: (_float_or_zero(p.cuantia), _text(p.cuantia))
    if column == "estado":
        return lambda p: (_text(p.status.value if p.status else ""),)
    return lambda p, c=column: (_text(getattr(p, c)),)


def sort_processes(
    processes: list[Process], sort: str | None, direction: str | None
) -> list[Process]:
    col = normalize_sort(sort)
    desc = normalize_dir(direction, col) == "desc"
    key_fn = sort_key(col)
    return sorted(processes, key=key_fn, reverse=desc)


def build_sort_query(
    column: str,
    *,
    sort: str,
    direction: str,
    estado: str = "",
    entidad: str = "",
    objeto: str = "",
) -> str:
    params: dict[str, str] = {
        "sort": column,
        "dir": toggle_dir(column, sort, direction),
    }
    if estado:
        params["estado"] = estado
    if entidad:
        params["entidad"] = entidad
    if objeto:
        params["objeto"] = objeto
    return "?" + urlencode(params)
