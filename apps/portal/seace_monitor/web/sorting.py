"""Ordenamiento de procesos en la UI."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlencode

from ..db.list_views import ProcessListView
from ..db.models import Process

_DATE_TIME_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?"
)

SORTABLE_COLUMNS: dict[str, str] = {
    "correlativo": "Correl.",
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

WORKFLOW_LIST_SORT_COLUMNS: dict[str, str] = {
    key: SORTABLE_COLUMNS[key]
    for key in (
        "correlativo",
        "fecha_publicacion",
        "entidad",
        "nomenclatura",
        "objeto",
        "descripcion",
        "fecha_consultas",
        "fecha_presentacion",
        "estado",
    )
}

DEFAULT_SORT = "fecha_publicacion"
WORKFLOW_LIST_DEFAULT_SORT = "correlativo"
DEFAULT_DIR = "desc"
DATE_COLUMNS = frozenset(
    {"fecha_publicacion", "fecha_consultas", "fecha_presentacion"}
)


def normalize_sort(sort: str | None, *, default: str = DEFAULT_SORT) -> str:
    if sort and sort in SORTABLE_COLUMNS:
        return sort
    return default


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


def parse_seace_datetime(value: str | None) -> float | None:
    if not value:
        return None
    m = _DATE_TIME_RE.match(value.strip())
    if not m:
        return None
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
        return None


def _date_sort_key(value: str | None, *, descending: bool = False) -> tuple[int, float, str]:
    """Fechas inválidas o vacías van al final en asc y desc."""
    ts = parse_seace_datetime(value)
    if ts is None:
        return (1, 0.0, _text(value))
    return (0, -ts if descending else ts, _text(value))


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
        return lambda p, c=column: _date_sort_key(getattr(p, c), descending=False)
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
    if col in DATE_COLUMNS:
        return sorted(
            processes,
            key=lambda p, c=col: _date_sort_key(getattr(p, c), descending=desc),
        )
    key_fn = sort_key(col)
    return sorted(processes, key=key_fn, reverse=desc)


def list_view_sort_key(
    column: str, *, descending: bool = False
) -> Callable[[ProcessListView], tuple[Any, ...]]:
    if column == "correlativo":
        return lambda v: (
            1 if v.correlativo is None else 0,
            -(v.correlativo or 0) if descending else (v.correlativo or 0),
        )
    if column == "numero":
        return lambda v: (_int_or_zero(v.process.numero), _text(v.process.numero))
    if column == "entidad":
        return lambda v: (_text(v.process.entity.nombre if v.process.entity else ""),)
    if column == "fecha_consultas":
        return lambda v: _date_sort_key(
            v.fin_consultas if v.fin_consultas != "—" else "", descending=descending
        )
    if column == "fecha_presentacion":
        return lambda v: _date_sort_key(
            v.fin_presentacion if v.fin_presentacion != "—" else "", descending=descending
        )
    if column == "fecha_publicacion":
        return lambda v: _date_sort_key(v.process.fecha_publicacion, descending=descending)
    if column == "cuantia":
        return lambda v: (_float_or_zero(v.process.cuantia), _text(v.process.cuantia))
    if column == "estado":
        return lambda v: (_text(v.process.status.value if v.process.status else ""),)
    if column in SORTABLE_COLUMNS:
        return lambda v, c=column: (_text(getattr(v.process, c)),)
    return lambda v: (_text(v.process.nomenclatura),)


def sort_process_list_views(
    views: list[ProcessListView],
    sort: str | None,
    direction: str | None,
    *,
    default_sort: str = DEFAULT_SORT,
) -> list[ProcessListView]:
    col = normalize_sort(sort, default=default_sort)
    desc = normalize_dir(direction, col) == "desc"
    if col in DATE_COLUMNS or col == "correlativo":
        key_fn = list_view_sort_key(col, descending=desc)
        return sorted(views, key=key_fn)
    key_fn = list_view_sort_key(col)
    return sorted(views, key=key_fn, reverse=desc)


def build_sort_query(
    column: str,
    *,
    sort: str,
    direction: str,
    estado: str = "",
    entidad: str = "",
    objeto: str = "",
    scroll: str = "",
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
    if scroll:
        params["scroll"] = scroll
    return "?" + urlencode(params)
