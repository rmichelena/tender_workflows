"""Tests TTL adaptativo del watchlist (W1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import AppConfig
from .db.models import Base, Entity, Process, ProcessStatus
from .watchlist_refresh import (
    watchlist_refresh_due,
    watchlist_refresh_seconds,
)


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20123456789", nombre="E", activa=True)
    session.add(entity)
    session.flush()
    return session, entity


def _proc(entity, **kwargs):
    defaults = dict(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="1",
        nid_proceso="1",
        nomenclatura="NOM-1",
        status=ProcessStatus.descargada,
    )
    defaults.update(kwargs)
    return Process(**defaults)


def test_urgent_ttl_when_presentacion_within_horizon():
    session, entity = _setup()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    # Fin presentación en ~24h (dentro de horizonte 48h).
    proc = _proc(
        entity,
        cronograma_json=json.dumps(
            [
                {
                    "etapa": "Presentación de propuestas",
                    "fecha_inicio": "04/06/2026 00:00",
                    "fecha_fin": "06/06/2026 18:00",
                }
            ]
        ),
        watch_checked_at=now - timedelta(minutes=50),
    )
    cfg = AppConfig(
        watchlist_refresh_interval="3h",
        watchlist_refresh_interval_urgent="45m",
        watchlist_urgent_horizon="48h",
    )
    assert watchlist_refresh_seconds(proc, cfg, now=now) == cfg.watchlist_refresh_urgent_seconds
    assert watchlist_refresh_due(proc, cfg, now=now) is True


def test_base_ttl_when_deadline_far():
    session, entity = _setup()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    proc = _proc(
        entity,
        cronograma_json=json.dumps(
            [
                {
                    "etapa": "Presentación de propuestas",
                    "fecha_inicio": "01/07/2026 00:00",
                    "fecha_fin": "15/07/2026 18:00",
                }
            ]
        ),
        watch_checked_at=now - timedelta(hours=1),
    )
    cfg = AppConfig()
    assert watchlist_refresh_seconds(proc, cfg, now=now) == cfg.watchlist_refresh_seconds
    assert watchlist_refresh_due(proc, cfg, now=now) is False


def test_never_checked_is_always_due():
    session, entity = _setup()
    proc = _proc(entity, watch_checked_at=None)
    cfg = AppConfig()
    assert watchlist_refresh_due(proc, cfg) is True


def test_urgent_ttl_after_deadline_within_horizon():
    # Post-deadline: fin presentación ya pasó pero dentro del look-back de 48h.
    _, entity = _setup()
    now = datetime(2026, 6, 7, 17, 0, tzinfo=timezone.utc)  # ~12:00 Lima
    proc = _proc(
        entity,
        cronograma_json=json.dumps(
            [
                {
                    "etapa": "Presentación de propuestas",
                    "fecha_inicio": "04/06/2026 00:00",
                    "fecha_fin": "06/06/2026 18:00",
                }
            ]
        ),
        watch_checked_at=now - timedelta(minutes=50),
    )
    cfg = AppConfig(
        watchlist_refresh_interval="3h",
        watchlist_refresh_interval_urgent="45m",
        watchlist_urgent_horizon="48h",
    )
    assert watchlist_refresh_seconds(proc, cfg, now=now) == cfg.watchlist_refresh_urgent_seconds
