"""Proxy HTTP hacia SEACE para navegación JSF completa en el navegador."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from fastapi import Request, Response
from fastapi.responses import RedirectResponse

from bs4 import BeautifulSoup

from ..client import ProcessRow
from ..config import AppConfig
from ..db.models import Process
from ..http_util import requests_proxies
from .seace_view import can_open_seace, row_from_list_html

logger = logging.getLogger(__name__)

SEACE_ORIGIN = "https://prod2.seace.gob.pe"
SEACE_APP = "/seacebus-uiwd-pub"
PROXY_ROOT = "/seace/p"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

REWRITE_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
)

_lock = threading.Lock()
_sessions: dict[str, tuple[requests.Session, float]] = {}
_SESSION_TTL_SECONDS = 3600
_SESSION_MAX = 200


def _create_session(http_proxy: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    proxies = requests_proxies(http_proxy)
    if proxies:
        session.proxies.update(proxies)
    return session


def _evict_stale_sessions(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [
        sid
        for sid, (_, ts) in _sessions.items()
        if now - ts > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        _sessions.pop(sid, None)
    while len(_sessions) > _SESSION_MAX:
        oldest = min(_sessions.items(), key=lambda item: item[1][1])[0]
        _sessions.pop(oldest, None)


def get_or_create_session(sid: str, http_proxy: str | None) -> requests.Session:
    now = time.time()
    with _lock:
        _evict_stale_sessions(now)
        if sid not in _sessions:
            _sessions[sid] = (_create_session(http_proxy), now)
        session, _ = _sessions[sid]
        _sessions[sid] = (session, now)
        return session


def new_session_id() -> str:
    return uuid.uuid4().hex


def seace_view_path(process_id: int) -> str:
    return f"/seace/open/{process_id}"


def seace_open_redirect(process: Process, *, sid: str) -> RedirectResponse:
    params = urlencode(
        {
            "ruc_entidad": process.entity.ruc,
            "anio": str(process.anio),
            "seace_open": str(process.id),
        }
    )
    target = f"{PROXY_ROOT}/buscadorPublico/ongei/buscadorPublico.xhtml?{params}"
    response = RedirectResponse(target, status_code=302)
    response.set_cookie(
        "seace_sid",
        sid,
        max_age=3600,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def _rewrite_url(url: str) -> str:
    if not url:
        return url
    for prefix in (
        f"{SEACE_ORIGIN}:443{SEACE_APP}/",
        f"{SEACE_ORIGIN}{SEACE_APP}/",
        f"{SEACE_APP}/",
    ):
        if url.startswith(prefix):
            suffix = url[len(prefix) :]
            return f"{PROXY_ROOT}/{suffix}"
    if url.startswith(PROXY_ROOT):
        return url
    return url


def _rewrite_text(text: str) -> str:
    text = text.replace(f"{SEACE_ORIGIN}:443{SEACE_APP}/", f"{PROXY_ROOT}/")
    text = text.replace(f"{SEACE_ORIGIN}{SEACE_APP}/", f"{PROXY_ROOT}/")
    text = re.sub(r'(?<=["\'])/seacebus-uiwd-pub/', f"{PROXY_ROOT}/", text)
    return text


def _buscador_list_url(process: Process) -> str:
    return (
        f"{SEACE_ORIGIN}{SEACE_APP}/buscadorPublico/ongei/buscadorPublico.xhtml"
        f"?ruc_entidad={process.entity.ruc}&anio={process.anio}"
    )


def _buscador_form_action(soup: BeautifulSoup, list_url: str) -> str:
    form = soup.find("form", id="formBuscador")
    if not form:
        raise RuntimeError("No se encontró formBuscador")
    action = form.get("action", "")
    if not action or action == ".":
        return list_url
    return urljoin(list_url, action)


def _proxy_location_from_absolute(absolute_url: str) -> str | None:
    if not absolute_url:
        return None
    parsed = urlparse(absolute_url)
    host = parsed.hostname or parsed.netloc.split(":", 1)[0]
    if not host.endswith("seace.gob.pe") or SEACE_APP not in parsed.path:
        return None
    suffix = parsed.path.split(SEACE_APP, 1)[1].lstrip("/")
    location = f"{PROXY_ROOT}/{suffix}"
    if parsed.query:
        location = f"{location}?{parsed.query}"
    return location


def _row_for_open(process: Process, list_html: str) -> ProcessRow | None:
    resolved = row_from_list_html(list_html, process.nid_proceso)
    if resolved is not None:
        if resolved.link_id != (process.link_id or ""):
            logger.info(
                "SEACE proxy: link_id %s → %s (nid=%s)",
                process.link_id,
                resolved.link_id,
                process.nid_proceso,
            )
        return resolved
    logger.warning(
        "SEACE proxy: fila no encontrada en listado vivo (nid=%s); "
        "no se usa link_id almacenado",
        process.nid_proceso,
    )
    return None


def _try_server_open_ficha(
    session: requests.Session,
    process: Process,
    list_html: str,
    list_url: str,
) -> str | None:
    soup = BeautifulSoup(list_html, "lxml")
    vs_el = soup.find("input", {"name": "javax.faces.ViewState"})
    if not vs_el or not vs_el.get("value"):
        return None
    row = _row_for_open(process, list_html)
    if row is None or not row.link_id:
        return None
    try:
        action = _buscador_form_action(soup, list_url)
    except RuntimeError:
        return None
    post_data = {
        "formBuscador": "formBuscador",
        "javax.faces.ViewState": vs_el["value"],
        "ntipo": row.ntipo,
        row.link_id: row.link_id,
        "nidConvocatoria": row.nid_convocatoria,
        "nidProceso": row.nid_proceso,
        "nidSistema": row.nid_sistema,
        "ptoRetorno": "LOCAL_ONGEI",
    }
    try:
        resp = session.post(
            action,
            data=post_data,
            headers={"User-Agent": USER_AGENT},
            timeout=60,
            allow_redirects=True,
        )
    except requests.RequestException:
        logger.exception(
            "SEACE proxy: falló POST apertura ficha nid=%s", process.nid_proceso
        )
        return None
    if "fichaSeleccion" not in resp.url:
        logger.warning(
            "SEACE proxy: POST ficha no redirigió (nid=%s url=%s)",
            process.nid_proceso,
            resp.url[:120],
        )
        return None
    return _proxy_location_from_absolute(resp.url)


def _auto_open_script(row: ProcessRow) -> str:
    payload: dict[str, str] = {
        "formBuscador": "formBuscador",
        "ntipo": row.ntipo,
        row.link_id: row.link_id,
        "nidConvocatoria": row.nid_convocatoria,
        "nidProceso": row.nid_proceso,
        "nidSistema": row.nid_sistema,
        "ptoRetorno": "LOCAL_ONGEI",
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    return (
        "<script>(function(){"
        "if(window.__seaceAutoOpen)return;"
        "window.__seaceAutoOpen=true;"
        "var form=document.getElementById('formBuscador');"
        "if(!form)return;"
        "var vs=document.querySelector('input[name=\"javax.faces.ViewState\"]');"
        "if(!vs)return;"
        "var f=document.createElement('form');"
        "f.method='POST';"
        "var action=form.getAttribute('action')||form.action||'';"
        "if(!action||action==='.'){action=window.location.pathname+window.location.search;}"
        "f.action=action;"
        "var payload="
        + payload_json
        + ";"
        "Object.keys(payload).forEach(function(k){"
        "var inp=document.createElement('input');"
        "inp.type='hidden';inp.name=k;inp.value=payload[k];f.appendChild(inp);"
        "});"
        "var vsInp=document.createElement('input');"
        "vsInp.type='hidden';vsInp.name='javax.faces.ViewState';vsInp.value=vs.value;"
        "f.appendChild(vsInp);"
        "document.body.appendChild(f);"
        "f.submit();"
        "})();</script>"
    )


def _inject_auto_open(html: str, process: Process, *, list_html: str) -> str:
    row = _row_for_open(process, list_html)
    if row is None or not row.link_id:
        return html
    script = _auto_open_script(row)
    if re.search(r"</body>", html, re.I):
        return re.sub(r"</body>", script + "</body>", html, count=1, flags=re.I)
    return html + script


def _upstream_path(path: str, query: str) -> str:
    url = f"{SEACE_APP}/{path}"
    if query:
        url = f"{url}?{query}"
    return url


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": USER_AGENT}
    if request.headers.get("accept"):
        headers["Accept"] = request.headers["accept"]
    if request.headers.get("accept-language"):
        headers["Accept-Language"] = request.headers["accept-language"]
    if request.headers.get("content-type") and request.method == "POST":
        headers["Content-Type"] = request.headers["content-type"]
    return headers


def _build_response(
    upstream: requests.Response,
    *,
    process_for_inject: Process | None = None,
    list_html_for_open: str | None = None,
) -> Response:
    if upstream.status_code in (301, 302, 303, 307, 308):
        location = upstream.headers.get("Location", "")
        if location:
            absolute = urljoin(f"{SEACE_ORIGIN}{SEACE_APP}/", location)
            parsed = urlparse(absolute)
            host = parsed.hostname or parsed.netloc.split(":", 1)[0]
            if host.endswith("seace.gob.pe") and SEACE_APP in parsed.path:
                suffix = parsed.path.split(SEACE_APP, 1)[1].lstrip("/")
                query = parsed.query
                location = f"{PROXY_ROOT}/{suffix}"
                if query:
                    location = f"{location}?{query}"
            return RedirectResponse(location, status_code=upstream.status_code)

    content = upstream.content
    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    media_type = content_type.split(";", 1)[0].strip().lower()

    if media_type in REWRITE_TYPES:
        text = content.decode(upstream.encoding or "utf-8", errors="replace")
        text = _rewrite_text(text)
        if process_for_inject is not None and "html" in media_type:
            source_html = list_html_for_open or text
            text = _inject_auto_open(text, process_for_inject, list_html=source_html)
        content = text.encode("utf-8")
        content_type = content_type.split(";", 1)[0] + "; charset=utf-8"

    response = Response(content=content, status_code=upstream.status_code)
    response.headers["Content-Type"] = content_type

    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in ("content-length", "content-encoding", "transfer-encoding", "set-cookie", "connection"):
            continue
        if lower == "location":
            continue
        response.headers[key] = value
    return response


def _clean_path(path: str) -> str:
    return path.split(";", 1)[0]


def proxy_seace_request(
    request: Request,
    path: str,
    config: AppConfig,
    process_for_open: Process | None,
    *,
    body: bytes | None = None,
) -> Response:
    sid = request.cookies.get("seace_sid")
    if not sid:
        return Response("Sesión SEACE no iniciada.", status_code=400)

    path = _clean_path(path)
    params = list(parse_qsl(str(request.url.query), keep_blank_values=True))
    inject_process = process_for_open
    upstream_params = [(k, v) for k, v in params if k != "seace_open"]
    query = urlencode(upstream_params)

    session = get_or_create_session(sid, config.http_proxy)
    upstream_url = _upstream_path(path, query)
    headers = _forward_headers(request)

    try:
        if request.method == "GET":
            upstream = session.get(
                f"{SEACE_ORIGIN}{upstream_url}",
                headers=headers,
                timeout=60,
                allow_redirects=False,
            )
        elif request.method == "HEAD":
            upstream = session.head(
                f"{SEACE_ORIGIN}{upstream_url}",
                headers=headers,
                timeout=60,
                allow_redirects=False,
            )
        else:
            payload = body if body is not None else b""
            upstream = session.post(
                f"{SEACE_ORIGIN}{upstream_url}",
                data=payload,
                headers=headers,
                timeout=120,
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        logger.exception("Proxy SEACE falló: %s", upstream_url)
        return Response(f"Error conectando con SEACE: {exc}", status_code=502)

    if (
        request.method == "GET"
        and inject_process is not None
        and "buscadorPublico.xhtml" in path
        and upstream.status_code == 200
    ):
        list_url = f"{SEACE_ORIGIN}{upstream_url}"
        proxied = _try_server_open_ficha(
            session,
            inject_process,
            upstream.text,
            list_url,
        )
        if proxied:
            return RedirectResponse(proxied, status_code=302)

    should_inject = inject_process is not None and "buscadorPublico.xhtml" in path
    return _build_response(
        upstream,
        process_for_inject=inject_process if should_inject else None,
        list_html_for_open=upstream.text if should_inject else None,
    )
