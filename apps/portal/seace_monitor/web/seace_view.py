"""Helpers para abrir fichas SEACE desde la UI."""

from __future__ import annotations

from ..db.models import Process
from ..seace_search import normalize_nomenclatura


def can_open_seace(process: Process) -> bool:
    return bool(process.entity and normalize_nomenclatura(process.nomenclatura))
