"""TTL adaptativo del watchlist (W1).

Procesos con hitos de cronograma cercanos (fin consultas / fin presentación) se
refrescan con un intervalo más corto; el resto mantiene el TTL base (p. ej. 3h).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .parser import CronogramaEtapa, extract_cronograma_fechas
from .scan_options import LIMA, parse_seace_date

if TYPE_CHECKING:
    from .config import AppConfig
    from .db.models import Process


def watchlist_refresh_due(
    proc: "Process", config: "AppConfig", now: datetime | None = None
) -> bool:
    """¿Toca refrescar este proceso del watchlist según su TTL (base o urgente)?"""
    now = _as_utc(now or datetime.now(timezone.utc))
    checked = proc.watch_checked_at
    if checked is None:
        return True
    checked = _as_utc(checked)
    interval = watchlist_refresh_seconds(proc, config, now=now)
    return checked < now - timedelta(seconds=interval)


def watchlist_refresh_seconds(
    proc: "Process", config: "AppConfig", now: datetime | None = None
) -> int:
    """Segundos de TTL aplicables a este proceso (urgente si hay hito cercano)."""
    if _has_urgent_cronograma_deadline(proc, config, now=now):
        return config.watchlist_refresh_urgent_seconds
    return config.watchlist_refresh_seconds


def _has_urgent_cronograma_deadline(
    proc: "Process", config: "AppConfig", now: datetime | None = None
) -> bool:
    """¿Algún hito clave del cronograma cae dentro del horizonte urgente?"""
    now_lima = (now or datetime.now(LIMA)).astimezone(LIMA)
    horizon = timedelta(seconds=config.watchlist_urgent_horizon_seconds)
    deadline = now_lima + horizon

    fechas = _key_deadline_datetimes(proc)
    for dt in fechas:
        if now_lima <= dt <= deadline:
            return True
    return False


def _key_deadline_datetimes(proc: "Process") -> list[datetime]:
    """Fechas fin de consultas y presentación (America/Lima) desde cronograma_json."""
    cronograma = _cronograma_from_json(proc.cronograma_json)
    if not cronograma:
        # Fallback a columnas materializadas en el listado.
        out: list[datetime] = []
        for raw in (proc.fecha_consultas, proc.fecha_presentacion):
            dt = parse_seace_date(raw)
            if dt is not None:
                out.append(dt)
        return out

    fechas = extract_cronograma_fechas(cronograma)
    out: list[datetime] = []
    for raw in (fechas.fecha_consultas, fechas.fecha_presentacion):
        dt = parse_seace_date(raw)
        if dt is not None:
            out.append(dt)
    return out


def _cronograma_from_json(raw: str | None) -> list[CronogramaEtapa]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [
        CronogramaEtapa(
            etapa=str(item.get("etapa", "")),
            fecha_inicio=str(item.get("fecha_inicio", "")),
            fecha_fin=str(item.get("fecha_fin", "")),
        )
        for item in data
        if isinstance(item, dict)
    ]


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
