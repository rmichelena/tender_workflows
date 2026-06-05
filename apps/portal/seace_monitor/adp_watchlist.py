"""Watchlist ADP: re-fetch de procesos descargados/analizados para detectar cambios.

Análogo a :func:`refresh_watchlist_processes` de SEACE, pero simplificado:
re-obtiene el HTML de la categoría y compara documentos.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from .adp_client import ALL_WORK_IDS, AdpClient
from .adp_downloader import download_adp_documents
from .adp_parser import parse_adp_html
from .adp_scanner import ADP_PORTAL_SOURCE, _adp_doc_to_dict, _adp_process_to_cronograma
from .config import AppConfig
from .db.models import Process, ProcessStatus, utcnow
from .document_storage import write_manifest

logger = logging.getLogger(__name__)

WATCHLIST_STATUSES = frozenset(
    {
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
)


def _merge_archivo_from_storage(
    new_docs: list[dict], old_documentos_json: str | None
) -> None:
    """Preserva ``archivo`` (path en disco) de documentos ya descargados.

    Análogo a :func:`watchlist._merge_parsed_docs_with_storage` de SEACE.
    """
    if not old_documentos_json:
        return
    try:
        old_docs = json.loads(old_documentos_json)
    except json.JSONDecodeError:
        return
    stored_by_namefile: dict[str, dict] = {}
    for d in old_docs:
        if isinstance(d, dict) and d.get("name_file") and d.get("archivo"):
            stored_by_namefile[d["name_file"]] = d
    for doc in new_docs:
        if not doc.get("name_file"):
            continue
        stored = stored_by_namefile.get(doc["name_file"])
        if stored and stored.get("archivo"):
            doc["archivo"] = stored["archivo"]


def _docs_fingerprint(docs_json: str | None) -> str:
    """Hash del contenido de documentos para comparación."""
    if not docs_json:
        return ""
    try:
        docs = json.loads(docs_json)
    except json.JSONDecodeError:
        return ""
    # Normalizar: solo campos relevantes, ordenados por name_file
    entries = []
    for d in sorted(docs, key=lambda x: x.get("name_file", "")):
        entries.append(
            f"{d.get('name_file', '')}|{d.get('title', '')}"
            f"|{d.get('vigencia_desde', '')}|{d.get('vigencia_hasta', '')}"
        )
    return hashlib.sha256("|".join(entries).encode()).hexdigest()


def refresh_adp_watchlist(config: AppConfig, session: Session) -> int:
    """Re-fetch procesos ADP en watchlist para detectar cambios.

    Returns:
        Número de procesos actualizados.
    """
    client = AdpClient(http_proxy=config.http_proxy)
    try:
        return _refresh_adp_watchlist_inner(config, session, client)
    finally:
        client.close()


def _refresh_adp_watchlist_inner(
    config: AppConfig, session: Session, client: AdpClient
) -> int:
    from .watchlist_refresh import watchlist_refresh_due

    now = utcnow()
    processes = [
        proc
        for proc in (
            session.query(Process)
            .options(joinedload(Process.entity))
            .filter(Process.source == ADP_PORTAL_SOURCE)
            .filter(Process.status.in_(tuple(WATCHLIST_STATUSES)))
            .all()
        )
        if watchlist_refresh_due(proc, config, now=now)
    ]

    if not processes:
        return 0

    # Agrupar por work_id inferido del código (necesitamos re-fetch de la categoría)
    # Estrategia: fetch todas las categorías una vez, indexar por código
    all_parsed: dict[str, tuple[int, object]] = {}
    for work_id in ALL_WORK_IDS:
        try:
            html = client.fetch_category_html(work_id)
            parsed = parse_adp_html(html, work_id)
            for p in parsed:
                all_parsed[p.code] = (work_id, p)
        except Exception:
            logger.exception("ADP watchlist: error fetching work_id=%s", work_id)

    updated = 0
    for proc in processes:
        savepoint = session.begin_nested()
        try:
            if _refresh_process(config, session, proc, all_parsed, client):
                updated += 1
            proc.watch_checked_at = utcnow()
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            logger.exception(
                "ADP watchlist: error proceso id=%s code=%s",
                proc.id,
                proc.source_ref,
            )

    return updated


def _refresh_process(
    config: AppConfig,
    session: Session,
    proc: Process,
    all_parsed: dict[str, tuple[int, object]],
    client: AdpClient,
) -> bool:
    """Compara y actualiza un proceso ADP con datos frescos del portal."""
    code = proc.source_ref
    if not code:
        return False

    entry = all_parsed.get(code)
    if not entry:
        logger.debug("ADP watchlist: código %s no encontrado en HTML actual", code)
        return False

    _work_id, adp_proc = entry
    new_docs_json = json.dumps(
        [_adp_doc_to_dict(d) for d in adp_proc.documents],
        ensure_ascii=False,
    )

    old_fp = _docs_fingerprint(proc.documentos_json)
    new_fp = _docs_fingerprint(new_docs_json)

    if old_fp == new_fp:
        return False

    # Hay cambios — preservar paths de archivos ya descargados
    new_docs = json.loads(new_docs_json)
    _merge_archivo_from_storage(new_docs, proc.documentos_json)
    # Siempre re-serializar después del merge para no perder archivo paths
    new_docs_json = json.dumps(new_docs, ensure_ascii=False)

    if proc.data_dir:
        docs_dir = Path(proc.data_dir) / "documentos"
        if docs_dir.is_dir():
            download_adp_documents(docs_dir, new_docs, client)
            new_docs_json = json.dumps(new_docs, ensure_ascii=False)

    # Guardar changelog previo
    if proc.documentos_json and not proc.watch_documentos_prev_json:
        proc.watch_documentos_prev_json = proc.documentos_json

    proc.documentos_json = new_docs_json
    proc.cronograma_json = json.dumps(
        _adp_process_to_cronograma(adp_proc),
        ensure_ascii=False,
    )
    proc.content_hash = adp_proc.content_hash()
    proc.updated_at = datetime.now(timezone.utc)
    proc.watch_unread = True

    # Actualizar manifest si existe data_dir
    if proc.data_dir:
        docs_dir = Path(proc.data_dir) / "documentos"
        if docs_dir.is_dir():
            write_manifest(docs_dir, new_docs)

    session.flush()
    logger.info(
        "ADP watchlist: cambios detectados code=%s id=%s",
        code,
        proc.id,
    )
    return True
