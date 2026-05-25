"""Comparación semántica de ficha SEACE para watchlist (ignora ruido de metadata)."""

from __future__ import annotations

import json

from .parser import clean_cronograma_etapa

_DOC_COMPARE_KEYS = ("nombre", "etapa", "tipo_documento", "tipo_descarga")


def _parse_json_list(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)]


def normalize_documento_entry(doc: dict) -> dict:
    return {
        "uuid": str(doc.get("uuid", "")).strip(),
        "nombre": str(doc.get("nombre", "")).strip(),
        "etapa": str(doc.get("etapa", "")).strip(),
        "tipo_documento": str(doc.get("tipo_documento", "")).strip(),
        "fecha_publicacion": str(doc.get("fecha_publicacion", "") or "").strip(),
        "tipo_descarga": str(doc.get("tipo_descarga", "3")).strip(),
    }


def normalize_cronograma_entry(item: dict) -> dict:
    return {
        "etapa": clean_cronograma_etapa(str(item.get("etapa", ""))),
        "fecha_inicio": str(item.get("fecha_inicio", "")).strip(),
        "fecha_fin": str(item.get("fecha_fin", "")).strip(),
    }


def _documento_entries_equal(old: dict, new: dict) -> bool:
    for key in _DOC_COMPARE_KEYS:
        if old[key] != new[key]:
            return False
    old_fp = old["fecha_publicacion"]
    new_fp = new["fecha_publicacion"]
    if old_fp and new_fp and old_fp != new_fp:
        return False
    return True


def documentos_semantically_equal(
    documentos_json_a: str | None, documentos_json_b: str | None
) -> bool:
    by_uuid_a = {
        item["uuid"]: item
        for item in (normalize_documento_entry(d) for d in _parse_json_list(documentos_json_a))
        if item["uuid"]
    }
    by_uuid_b = {
        item["uuid"]: item
        for item in (normalize_documento_entry(d) for d in _parse_json_list(documentos_json_b))
        if item["uuid"]
    }
    if set(by_uuid_a) != set(by_uuid_b):
        return False
    return all(
        _documento_entries_equal(by_uuid_a[uuid], by_uuid_b[uuid]) for uuid in by_uuid_a
    )


def cronograma_semantically_equal(
    cronograma_json_a: str | None, cronograma_json_b: str | None
) -> bool:
    rows_a = [
        normalize_cronograma_entry(item) for item in _parse_json_list(cronograma_json_a)
    ]
    rows_b = [
        normalize_cronograma_entry(item) for item in _parse_json_list(cronograma_json_b)
    ]
    return rows_a == rows_b


def watchlist_content_changed(
    *,
    cronograma_json: str | None,
    documentos_json: str | None,
    new_cronograma_json: str,
    new_documentos_json: str,
) -> tuple[bool, bool]:
    """Retorna (cron_changed, docs_changed) usando comparación semántica."""
    cron_changed = not cronograma_semantically_equal(
        cronograma_json, new_cronograma_json
    )
    docs_changed = not documentos_semantically_equal(
        documentos_json, new_documentos_json
    )
    return cron_changed, docs_changed
