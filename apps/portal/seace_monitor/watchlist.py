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

from .client import ProcessRow, SeaceClient
from .config import AppConfig
from .db.models import Process, ProcessStatus, utcnow
from .document_storage import (
    normalize_legacy_filenames,
    prepare_download_dest,
    write_manifest,
)
from .downloader import download_file
from .parser import extract_cronograma_fechas, parse_ficha

logger = logging.getLogger(__name__)

WATCHLIST_STATUSES = frozenset(
    {
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
)


def _row_from_process(process: Process) -> ProcessRow:
    return ProcessRow(
        row_index=0,
        numero=process.numero or "",
        fecha_publicacion=process.fecha_publicacion or "",
        nomenclatura=process.nomenclatura,
        reiniciado_desde=process.reiniciado_desde or "",
        objeto=process.objeto or "",
        descripcion=process.descripcion or "",
        cuantia=process.cuantia or "",
        moneda=process.moneda or "",
        version_seace=process.version_seace or "",
        nid_proceso=process.nid_proceso,
        nid_convocatoria=process.nid_convocatoria or "",
        nid_sistema=process.nid_sistema or "3",
        link_id=process.link_id or "",
        ntipo=process.ntipo or "0",
    )


def watchlist_fingerprint(
    *,
    cronograma_json: str | None,
    documentos_json: str | None,
    fecha_publicacion: str | None = None,
) -> str:
    try:
        cronograma = json.loads(cronograma_json or "[]")
    except json.JSONDecodeError:
        cronograma = []
    try:
        documentos = json.loads(documentos_json or "[]")
    except json.JSONDecodeError:
        documentos = []
    payload = {
        "cronograma": cronograma,
        "documentos": documentos,
        "fecha_publicacion": fecha_publicacion or "",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


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


def _refresh_watchlist_process(
    config: AppConfig, session: Session, process: Process
) -> bool:
    if not process.entity:
        return False
    if not process.nid_convocatoria or not process.link_id:
        logger.warning(
            "Watchlist: sin metadatos ficha id=%s nid=%s",
            process.id,
            process.nid_proceso,
        )
        return False

    client = SeaceClient(
        process.entity.ruc,
        process.anio,
        config.rows_per_page,
        http_proxy=config.http_proxy,
    )
    row = _row_from_process(process)
    ficha_result = client.open_ficha(row)
    ficha = parse_ficha(ficha_result.html, ficha_result.ficha_id, process.nid_proceso)

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
    if old_fp == new_fp:
        return False

    cron_changed = (process.cronograma_json or "") != new_cron_json
    docs_changed = (process.documentos_json or "") != new_docs_json
    new_docs = json.loads(new_docs_json)

    # Descargar antes de mutar documentos_json para que un fallo no suprima reintentos.
    if docs_changed and process.data_dir:
        _download_new_documents(config, process, new_docs)

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


def _download_new_documents(
    config: AppConfig, process: Process, docs: list[dict]
) -> None:
    docs_dir = Path(process.data_dir) / "documentos"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        uuid = doc.get("uuid", "")
        if not uuid:
            continue
        dest, exists = prepare_download_dest(docs_dir, doc)
        if exists:
            continue
        tipo = doc.get("tipo_descarga", "3")
        download_file(
            uuid, dest, guest=tipo != "3", http_proxy=config.http_proxy
        )
        logger.info(
            "Watchlist: descargado doc nuevo %s → %s (proceso %s)",
            doc.get("nombre", uuid),
            dest.name,
            process.id,
        )
    normalize_legacy_filenames(docs_dir, docs)
    write_manifest(docs_dir, docs)
