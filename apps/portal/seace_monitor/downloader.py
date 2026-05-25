"""Descarga de documentos vía Alfresco (mismo mecanismo que cmsDescarga.js)."""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from urllib.parse import unquote

import requests

from .http_util import requests_proxies

ALFRESCO_API = (
    "https://alfprod.seace.gob.pe/alfresco/service/osce/downloadDoc"
)
ALFRESCO_BASE = "https://alfprod.seace.gob.pe/alfresco"
ALFRESCO_BASE_OP = "https://prodcont2.seace.gob.pe/alfresco"

_DOWNLOAD_RETRIES = 3
_DOWNLOAD_BACKOFF_SECONDS = (1.0, 2.0)


def filename_from_content_disposition(header: str | None) -> str | None:
    """Extrae el nombre de archivo del header Content-Disposition."""
    if not header:
        return None
    match = re.search(
        r"filename\*=(?:UTF-8''|utf-8'')(.*?)(?:;|$)",
        header,
        flags=re.IGNORECASE,
    )
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="([^"]+)"', header, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"filename=([^;]+)", header, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    return None


def resolve_download_url(
    doc_uuid: str, guest: bool = False, http_proxy: str | None = None
) -> str:
    """Obtiene URL de descarga directa para un documento."""
    callback = f"c{random.randint(1, 100_000_000)}"
    params = {"id": doc_uuid, "doc": callback, "guest": str(guest).lower()}
    r = requests.get(
        ALFRESCO_API, params=params, timeout=30, proxies=requests_proxies(http_proxy)
    )
    r.raise_for_status()

    m = re.search(r"\{.*\}", r.text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Respuesta Alfresco inválida para {doc_uuid}")

    data = json.loads(m.group())
    result = str(data.get("result", ""))
    path = data.get("downloadUrl", "")
    if not path:
        raise RuntimeError(f"Sin downloadUrl para {doc_uuid} (result={result})")

    if result == "200":
        return ALFRESCO_BASE + path
    if result == "201" and path:
        return ALFRESCO_BASE_OP + path
    raise RuntimeError(f"Alfresco result={result} para documento {doc_uuid}")


def _download_file_once(
    doc_uuid: str, dest: Path, guest: bool = False, http_proxy: str | None = None
) -> tuple[Path, str | None]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = resolve_download_url(doc_uuid, guest=guest, http_proxy=http_proxy)
    proxies = requests_proxies(http_proxy)
    r = requests.get(url, timeout=120, stream=True, proxies=proxies)
    r.raise_for_status()
    server_filename = filename_from_content_disposition(
        r.headers.get("Content-Disposition")
    )

    part = dest.with_name(f"{dest.name}.part")
    if part.exists():
        part.unlink()
    written = 0
    try:
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    if written == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Descarga vacía para documento {doc_uuid}")
    part.replace(dest)
    return dest, server_filename


def download_file(
    doc_uuid: str, dest: Path, guest: bool = False, http_proxy: str | None = None
) -> tuple[Path, str | None]:
    last_error: Exception | None = None
    for attempt in range(_DOWNLOAD_RETRIES):
        try:
            return _download_file_once(
                doc_uuid, dest, guest=guest, http_proxy=http_proxy
            )
        except (requests.RequestException, RuntimeError, OSError) as exc:
            last_error = exc
            if attempt < _DOWNLOAD_RETRIES - 1:
                backoff = _DOWNLOAD_BACKOFF_SECONDS[attempt]
                time.sleep(backoff)
    assert last_error is not None
    raise last_error
