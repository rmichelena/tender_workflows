"""Utilidades HTTP compartidas."""

from __future__ import annotations


def requests_proxies(http_proxy: str | None) -> dict[str, str] | None:
    if not http_proxy or not str(http_proxy).strip():
        return None
    url = str(http_proxy).strip()
    return {"http": url, "https": url}
