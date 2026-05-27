"""Query string helpers para filtros de la UI."""

from __future__ import annotations

from urllib.parse import urlencode


def publicaciones_query(
    *,
    entidad: str = "",
    objeto: str = "",
    sort: str = "",
    dir: str = "",
    msg: str = "",
    scroll: str = "",
) -> str:
    params: dict[str, str] = {}
    if entidad:
        params["entidad"] = entidad
    if objeto:
        params["objeto"] = objeto
    if sort:
        params["sort"] = sort
    if dir:
        params["dir"] = dir
    if msg:
        params["msg"] = msg
    if scroll:
        params["scroll"] = scroll
    if not params:
        return "/publicaciones"
    return "/publicaciones?" + urlencode(params)


def workflow_list_query(
    path: str,
    *,
    sort: str = "",
    dir: str = "",
    scroll: str = "",
) -> str:
    params: dict[str, str] = {}
    if sort:
        params["sort"] = sort
    if dir:
        params["dir"] = dir
    if scroll:
        params["scroll"] = scroll
    if not params:
        return path
    return path + "?" + urlencode(params)
