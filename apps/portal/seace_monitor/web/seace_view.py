"""Helpers para abrir fichas SEACE desde la UI."""

from __future__ import annotations

from ..client import ProcessRow
from ..db.models import Process


def can_open_seace(process: Process) -> bool:
    return bool(
        process.nid_proceso
        and process.nid_convocatoria
        and process.link_id
        and process.entity
    )


def process_row_from_model(process: Process) -> ProcessRow:
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
