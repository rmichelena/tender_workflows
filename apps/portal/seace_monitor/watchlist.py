"""Refresh periódico de procesos descargados/analizados (watchlist SEACE)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from .config import AppConfig
from .analysis.document_prep import ARCHIVE_SUFFIXES, extract_archives
from .db.models import Process, ProcessStatus, utcnow
from .document_storage import (
    download_and_store_document,
    looks_like_size_label,
    normalize_legacy_filenames,
    prefer_canonical_archivo,
    resolve_existing_download,
    sync_doc_from_existing,
    write_manifest,
)
from .parser import extract_cronograma_fechas, parse_ficha
from .seace_search import (
    apply_list_row_to_process,
    normalize_nomenclatura,
    open_ficha_for_process,
)
from .watchlist_changelog import append_watchlist_changelog, build_watchlist_changelog_entry
from .watchlist_compare import watchlist_content_changed

logger = logging.getLogger(__name__)

WATCHLIST_STATUSES = frozenset(
    {
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
)


def watchlist_fingerprint(
    *,
    cronograma_json: str | None,
    documentos_json: str | None,
    fecha_publicacion: str | None = None,
) -> str:
    from .watchlist_compare import (
        documento_fingerprint_entry,
        normalize_cronograma_entry,
        _parse_json_list,
    )

    cronograma = [
        normalize_cronograma_entry(item) for item in _parse_json_list(cronograma_json)
    ]
    documentos = sorted(
        (
            documento_fingerprint_entry(item)
            for item in _parse_json_list(documentos_json)
            if documento_fingerprint_entry(item)["uuid"]
        ),
        key=lambda item: item["uuid"],
    )
    payload = {
        "cronograma": cronograma,
        "documentos": documentos,
        "fecha_publicacion": (fecha_publicacion or "").strip(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _process_fecha_changed(old: str | None, new: str | None) -> bool:
    return (old or "").strip() != (new or "").strip()


def _document_uuids_from_json(documentos_json: str | None) -> set[str]:
    if not documentos_json:
        return set()
    try:
        raw = json.loads(documentos_json)
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("uuid", "")).strip()
        for item in raw
        if isinstance(item, dict) and str(item.get("uuid", "")).strip()
    }


def _json_list_has_items(raw_json: str | None) -> bool:
    if not raw_json:
        return False
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    return isinstance(raw, list) and bool(raw)


def _validate_watchlist_ficha(process: Process, ficha) -> None:
    had_content = _json_list_has_items(process.cronograma_json) or _json_list_has_items(
        process.documentos_json
    )
    if had_content and not ficha.cronograma and not ficha.documentos:
        raise RuntimeError(
            f"Watchlist: ficha vacía para proceso id={process.id} nid={process.nid_proceso}; "
            "se conserva cronograma/documentos actuales"
        )


def _stored_docs_by_uuid(documentos_json: str | None) -> dict[str, dict]:
    if not documentos_json:
        return {}
    try:
        raw = json.loads(documentos_json)
    except json.JSONDecodeError:
        return {}
    by_uuid: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid", "")).strip()
        if uuid:
            by_uuid[uuid] = item
    return by_uuid


def _merge_parsed_docs_with_storage(
    parsed_docs: list[dict], stored_documentos_json: str | None
) -> list[dict]:
    """Conserva archivo/nombre en disco cuando la ficha no cambió semánticamente."""
    stored_by_uuid = _stored_docs_by_uuid(stored_documentos_json)
    merged: list[dict] = []
    for doc in parsed_docs:
        if not isinstance(doc, dict):
            continue
        uuid = str(doc.get("uuid", "")).strip()
        merged_doc = dict(doc)
        stored = stored_by_uuid.get(uuid)
        if stored:
            archivo = str(stored.get("archivo", "") or "").strip()
            nombre = str(stored.get("nombre", "") or "").strip()
            if archivo:
                merged_doc["archivo"] = archivo
            if nombre and not looks_like_size_label(nombre):
                merged_doc["nombre"] = nombre
        merged.append(merged_doc)
    return merged


def mark_watchlist_read(process: Process) -> None:
    process.watch_unread = False
    process.watch_cronograma_prev_json = None
    process.watch_documentos_prev_json = None


def watchlist_nav_badges(session: Session) -> dict[str, int]:
    descargados = (
        session.query(Process)
        .filter(
            Process.status == ProcessStatus.descargada,
            Process.watch_unread.is_(True),
        )
        .count()
    )
    analizados = (
        session.query(Process)
        .filter(
            Process.status.in_((ProcessStatus.analizada, ProcessStatus.portafolio)),
            Process.watch_unread.is_(True),
        )
        .count()
    )
    return {"descargados": descargados, "analizados": analizados}


def refresh_watchlist_processes(config: AppConfig, session: Session) -> int:
    """Re-fetch ficha SEACE para procesos en watchlist cuyo TTL venció."""
    threshold = utcnow() - config.watchlist_refresh_timedelta
    processes = (
        session.query(Process)
        .options(joinedload(Process.entity))
        .filter(Process.status.in_(tuple(WATCHLIST_STATUSES)))
        .filter(
            or_(
                Process.watch_checked_at.is_(None),
                Process.watch_checked_at < threshold,
            )
        )
        .all()
    )
    updated = 0
    for proc in processes:
        savepoint = session.begin_nested()
        try:
            if _refresh_watchlist_process(config, session, proc):
                updated += 1
            proc.watch_checked_at = utcnow()
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            logger.exception(
                "Watchlist: falló refresh proceso id=%s nid=%s",
                proc.id,
                proc.nid_proceso,
            )
    return updated


def _repair_document_metadata(process: Process) -> bool:
    """Corrige nombres erróneos en documentos_json usando manifest/disco."""
    if not process.data_dir or not process.documentos_json:
        return False
    docs_dir = Path(process.data_dir) / "documentos"
    if not docs_dir.is_dir():
        return False
    try:
        docs = json.loads(process.documentos_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(docs, list):
        return False

    changed = False
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        before = json.dumps(doc, sort_keys=True, ensure_ascii=False)
        sync_doc_from_existing(docs_dir, doc)
        if json.dumps(doc, sort_keys=True, ensure_ascii=False) != before:
            changed = True

    if not changed:
        return False

    normalize_legacy_filenames(docs_dir, docs)
    for doc in docs:
        prefer_canonical_archivo(docs_dir, doc)
    write_manifest(docs_dir, docs)
    process.documentos_json = json.dumps(docs, ensure_ascii=False)
    return True


def _refresh_watchlist_process(
    config: AppConfig, session: Session, process: Process
) -> bool:
    if not process.entity:
        return False
    if not normalize_nomenclatura(process.nomenclatura):
        logger.warning(
            "Watchlist: sin nomenclatura id=%s nid=%s",
            process.id,
            process.nid_proceso,
        )
        return False

    _repair_document_metadata(process)
    if process.data_dir:
        docs_dir = Path(process.data_dir) / "documentos"
        if docs_dir.is_dir():
            _ensure_archives_extracted(docs_dir)

    try:
        row, ficha_result, _client = open_ficha_for_process(config, process)
    except RuntimeError:
        logger.warning(
            "Watchlist: fila no encontrada id=%s nomenclatura=%s",
            process.id,
            process.nomenclatura,
        )
        return False
    ficha = parse_ficha(
        ficha_result.html,
        ficha_result.ficha_id,
        row.nid_proceso,
        http_session=_client.session,
        ficha_url=ficha_result.url,
    )
    _validate_watchlist_ficha(process, ficha)

    new_cron_json = json.dumps(
        [asdict(c) for c in ficha.cronograma], ensure_ascii=False
    )
    new_docs_json = json.dumps(
        [asdict(d) for d in ficha.documentos], ensure_ascii=False
    )
    old_fp = watchlist_fingerprint(
        cronograma_json=process.cronograma_json,
        documentos_json=process.documentos_json,
        fecha_publicacion=process.fecha_publicacion,
    )
    new_fp = watchlist_fingerprint(
        cronograma_json=new_cron_json,
        documentos_json=new_docs_json,
        fecha_publicacion=ficha.fecha_publicacion or process.fecha_publicacion,
    )
    cron_changed, docs_changed = watchlist_content_changed(
        cronograma_json=process.cronograma_json,
        documentos_json=process.documentos_json,
        new_cronograma_json=new_cron_json,
        new_documentos_json=new_docs_json,
    )
    new_fecha_publicacion = ficha.fecha_publicacion or process.fecha_publicacion
    fecha_changed = _process_fecha_changed(
        process.fecha_publicacion, new_fecha_publicacion
    )
    if old_fp == new_fp or (not cron_changed and not docs_changed and not fecha_changed):
        return False

    new_docs = json.loads(new_docs_json)

    if docs_changed and process.data_dir:
        _download_new_documents(
            config,
            process,
            new_docs,
            old_documentos_json=process.documentos_json,
        )
        new_docs_json = json.dumps(new_docs, ensure_ascii=False)
    else:
        new_docs = _merge_parsed_docs_with_storage(new_docs, process.documentos_json)
        new_docs_json = json.dumps(new_docs, ensure_ascii=False)

    changelog_entry = build_watchlist_changelog_entry(
        old_cronograma_json=process.cronograma_json,
        new_cronograma_json=new_cron_json,
        old_documentos_json=process.documentos_json,
        new_documentos_json=new_docs_json,
        old_fecha_publicacion=process.fecha_publicacion,
        new_fecha_publicacion=new_fecha_publicacion,
    )
    append_watchlist_changelog(process, changelog_entry)

    if cron_changed and process.cronograma_json:
        if not process.watch_unread or not process.watch_cronograma_prev_json:
            process.watch_cronograma_prev_json = process.cronograma_json
    if docs_changed and process.documentos_json:
        if not process.watch_unread or not process.watch_documentos_prev_json:
            process.watch_documentos_prev_json = process.documentos_json

    fechas = extract_cronograma_fechas(ficha.cronograma)
    process.cronograma_json = new_cron_json
    process.fecha_consultas = fechas.fecha_consultas
    process.fecha_presentacion = fechas.fecha_presentacion
    if ficha.fecha_publicacion:
        process.fecha_publicacion = ficha.fecha_publicacion
    process.content_hash = ficha.content_hash()
    process.ficha_id = ficha.ficha_id
    process.ficha_url = ficha_result.url
    apply_list_row_to_process(process, row)
    process.documentos_json = new_docs_json
    process.updated_at = datetime.now(timezone.utc)
    process.watch_unread = True

    session.flush()
    logger.info(
        "Watchlist: cambios id=%s nid=%s cron=%s docs=%s",
        process.id,
        process.nid_proceso,
        cron_changed,
        docs_changed,
    )
    return True


def _ensure_archives_extracted(docs_dir: Path) -> None:
    """Descomprime archivos en disco que aún no tienen carpeta en _extracted/."""
    for path in docs_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in ARCHIVE_SUFFIXES:
            continue
        dest = docs_dir / "_extracted" / path.stem
        if not dest.is_dir() or not any(dest.rglob("*")):
            extract_archives(docs_dir)
            return


def _download_new_documents(
    config: AppConfig,
    process: Process,
    docs: list[dict],
    *,
    old_documentos_json: str | None,
) -> None:
    docs_dir = Path(process.data_dir) / "documentos"
    docs_dir.mkdir(parents=True, exist_ok=True)
    old_uuids = _document_uuids_from_json(old_documentos_json)
    new_uuids = {
        str(doc.get("uuid", "")).strip()
        for doc in docs
        if isinstance(doc, dict) and str(doc.get("uuid", "")).strip()
    }
    added_uuids = new_uuids - old_uuids
    for doc in docs:
        uuid = doc.get("uuid", "")
        if not uuid:
            continue
        tipo = doc.get("tipo_descarga", "3")
        try:
            if download_and_store_document(
                docs_dir,
                doc,
                guest=tipo != "3",
                http_proxy=config.http_proxy,
            ):
                logger.info(
                    "Watchlist: descargado doc nuevo %s → %s (proceso %s)",
                    uuid,
                    doc.get("archivo", ""),
                    process.id,
                )
        except Exception as exc:
            raise RuntimeError(
                f"Watchlist: fallo al descargar documento {uuid} (proceso {process.id})"
            ) from exc
    normalize_legacy_filenames(docs_dir, docs)
    for doc in docs:
        prefer_canonical_archivo(docs_dir, doc)
    for uuid in added_uuids:
        doc = next((item for item in docs if item.get("uuid") == uuid), None)
        if doc is None:
            continue
        existing = resolve_existing_download(docs_dir, doc)
        if existing is None or existing.stat().st_size == 0:
            raise RuntimeError(
                f"Watchlist: documento nuevo {uuid} no quedó en disco (proceso {process.id})"
            )
    write_manifest(docs_dir, docs)
    extract_archives(docs_dir)
