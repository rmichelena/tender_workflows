"""Promoción feed→pipeline (0.3d).

La promoción es un **latch de un solo sentido** sobre el `Process` (mientras el split
físico de 0.3e no ocurre): `promoted_at IS NULL` = feed puro (descubrimiento ruidoso,
purgable); `promoted_at` seteado = trabajo curado (pipeline) que nunca debe purgarse ni
borrarse como duplicado de feed.

Una acción positiva del usuario (descargar/analizar/portafolio/marcar interés) llama a
``promote(session, process)``. El backfill histórico vive en ``db/session.py``
(``_backfill_promoted_at``); ``should_be_promoted`` replica su predicado para tests y para
marcar items que ya nacen curados.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..db.models import InterestStatus, ProcessStatus, utcnow

if TYPE_CHECKING:  # pragma: no cover
    from ..db.models import Process

# Estados que implican trabajo curado (espejo de `_PROMOTED_STATUSES` en `db/session.py`).
PROMOTED_STATUSES: frozenset[ProcessStatus] = frozenset(
    {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.descartando,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
        ProcessStatus.archivando,
        ProcessStatus.archivada,
    }
)


def is_promoted(process: "Process") -> bool:
    """¿El item dejó de ser feed puro? (tiene trabajo curado)."""
    return process.promoted_at is not None


def should_be_promoted(process: "Process") -> bool:
    """Predicado puro: ¿el item ya califica como curado por su estado actual?

    Espejo del backfill (`_backfill_promoted_at`): status de pipeline, interés marcado,
    descarga (`data_dir`) o análisis presente. No considera `promoted_at` (es el insumo
    para setearlo).
    """
    if process.status in PROMOTED_STATUSES:
        return True
    if process.interest_status not in (None, InterestStatus.none):
        return True
    if process.data_dir:
        return True
    if process.analysis is not None:
        return True
    return False


def promote(session, process: "Process") -> bool:
    """Marca el item como promovido (latch). Idempotente: no re-escribe si ya lo está.

    Devuelve ``True`` si setea el timestamp (transición feed→pipeline), ``False`` si ya
    estaba promovido. No hace commit (responsabilidad del caller).
    """
    if process.promoted_at is not None:
        return False
    process.promoted_at = utcnow()
    return True
