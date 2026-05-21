"""Descarga de documentos vía Alfresco (mismo mecanismo que cmsDescarga.js)."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import requests

from .http_util import requests_proxies

ALFRESCO_API = (
    "https://alfprod.seace.gob.pe/alfresco/service/osce/downloadDoc"
)
ALFRESCO_BASE = "https://alfprod.seace.gob.pe/alfresco"
ALFRESCO_BASE_OP = "https://prodcont2.seace.gob.pe/alfresco"


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


def download_file(
    doc_uuid: str, dest: Path, guest: bool = False, http_proxy: str | None = None
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = resolve_download_url(doc_uuid, guest=guest, http_proxy=http_proxy)
    proxies = requests_proxies(http_proxy)
    r = requests.get(url, timeout=120, stream=True, proxies=proxies)
    r.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
    return dest
