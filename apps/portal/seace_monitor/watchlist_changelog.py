"""Historial de cambios detectados por el watchlist SEACE."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .watchlist_compare import (
    _DOC_COMPARE_KEYS,
    _parse_json_list,
    documento_fingerprint_entry,
    normalize_cronograma_entry,
    normalize_documento_entry,
)

_MAX_ENTRIES = 100

_DOC_FIELD_LABELS = {
    "etapa": "Etapa",
    "tipo_documento": "Tipo",
    "tipo_descarga": "Acceso",
    "fecha_publicacion": "Fecha publicación",
}

_CRON_FIELD_LABELS = {
    "fecha_inicio": "Inicio",
    "fecha_fin": "Fin",
}


def build_watchlist_changelog_entry(
    *,
    old_cronograma_json: str | None,
    new_cronograma_json: str,
    old_documentos_json: str | None,
    new_documentos_json: str,
    old_fecha_publicacion: str | None,
    new_fecha_publicacion: str | None,
) -> dict:
    changes: list[dict] = []
    changes.extend(
        _diff_cronograma(old_cronograma_json, new_cronograma_json)
    )
    changes.extend(
        _diff_documentos(old_documentos_json, new_documentos_json)
    )
    old_fp = (old_fecha_publicacion or "").strip()
    new_fp = (new_fecha_publicacion or "").strip()
    if old_fp and new_fp and old_fp != new_fp:
        changes.append(
            {
                "area": "proceso",
                "label": "Fecha publicación (ficha)",
                "field": "fecha_publicacion",
                "kind": "modified",
                "old": old_fp,
                "new": new_fp,
            }
        )
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "changes": changes,
    }


def append_watchlist_changelog(process, entry: dict) -> None:
    if not entry.get("changes"):
        return
    try:
        rows = json.loads(process.watch_changelog_json or "[]")
    except json.JSONDecodeError:
        rows = []
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, entry)
    process.watch_changelog_json = json.dumps(rows[:_MAX_ENTRIES], ensure_ascii=False)


def _diff_cronograma(old_json: str | None, new_json: str) -> list[dict]:
    old_by_etapa = {
        normalize_cronograma_entry(item)["etapa"]: normalize_cronograma_entry(item)
        for item in _parse_json_list(old_json)
        if normalize_cronograma_entry(item)["etapa"]
    }
    new_by_etapa = {
        normalize_cronograma_entry(item)["etapa"]: normalize_cronograma_entry(item)
        for item in _parse_json_list(new_json)
        if normalize_cronograma_entry(item)["etapa"]
    }
    changes: list[dict] = []
    for etapa, row in new_by_etapa.items():
        if etapa not in old_by_etapa:
            changes.append(
                {
                    "area": "cronograma",
                    "label": etapa,
                    "field": "etapa",
                    "kind": "added",
                    "old": "",
                    "new": f"Inicio {row['fecha_inicio']} · Fin {row['fecha_fin']}",
                }
            )
            continue
        old_row = old_by_etapa[etapa]
        for field in ("fecha_inicio", "fecha_fin"):
            if old_row[field] == row[field]:
                continue
            changes.append(
                {
                    "area": "cronograma",
                    "label": f"{etapa} · {_CRON_FIELD_LABELS[field]}",
                    "field": field,
                    "kind": "modified",
                    "old": old_row[field] or "—",
                    "new": row[field] or "—",
                }
            )
    for etapa, row in old_by_etapa.items():
        if etapa in new_by_etapa:
            continue
        changes.append(
            {
                "area": "cronograma",
                "label": etapa,
                "field": "etapa",
                "kind": "removed",
                "old": f"Inicio {row['fecha_inicio']} · Fin {row['fecha_fin']}",
                "new": "",
            }
        )
    return changes


def _doc_label(doc: dict) -> str:
    tipo = str(doc.get("tipo_documento", "") or "").strip()
    etapa = str(doc.get("etapa", "") or "").strip()
    if tipo and etapa:
        return f"{etapa} · {tipo}"
    return tipo or etapa or str(doc.get("uuid", ""))[:8]


def _diff_documentos(old_json: str | None, new_json: str) -> list[dict]:
    old_by_uuid = {
        item["uuid"]: item
        for item in (
            documento_fingerprint_entry(d) for d in _parse_json_list(old_json)
        )
        if item["uuid"]
    }
    new_by_uuid = {
        item["uuid"]: item
        for item in (
            documento_fingerprint_entry(d) for d in _parse_json_list(new_json)
        )
        if item["uuid"]
    }
    old_full = {
        str(d.get("uuid", "")).strip(): normalize_documento_entry(d)
        for d in _parse_json_list(old_json)
        if str(d.get("uuid", "")).strip()
    }
    new_full = {
        str(d.get("uuid", "")).strip(): normalize_documento_entry(d)
        for d in _parse_json_list(new_json)
        if str(d.get("uuid", "")).strip()
    }
    changes: list[dict] = []
    for uuid, row in new_by_uuid.items():
        label = _doc_label(new_full.get(uuid, row))
        if uuid not in old_by_uuid:
            changes.append(
                {
                    "area": "documento",
                    "label": label,
                    "field": "documento",
                    "kind": "added",
                    "old": "",
                    "new": label,
                }
            )
            continue
        old_row = old_by_uuid[uuid]
        for field in _DOC_COMPARE_KEYS:
            if old_row[field] == row[field]:
                continue
            changes.append(
                {
                    "area": "documento",
                    "label": label,
                    "field": field,
                    "kind": "modified",
                    "old": old_row[field] or "—",
                    "new": row[field] or "—",
                }
            )
        old_fp = old_row["fecha_publicacion"]
        new_fp = row["fecha_publicacion"]
        if old_fp and new_fp and old_fp != new_fp:
            changes.append(
                {
                    "area": "documento",
                    "label": label,
                    "field": "fecha_publicacion",
                    "kind": "modified",
                    "old": old_fp,
                    "new": new_fp,
                }
            )
    for uuid, row in old_by_uuid.items():
        if uuid in new_by_uuid:
            continue
        label = _doc_label(old_full.get(uuid, row))
        changes.append(
            {
                "area": "documento",
                "label": label,
                "field": "documento",
                "kind": "removed",
                "old": label,
                "new": "",
            }
        )
    return changes
