"""Tests para fechas clave del cronograma."""

from .parser import CronogramaEtapa, extract_cronograma_fechas


def test_extract_cronograma_fechas_uses_fin():
    cronograma = [
        CronogramaEtapa(
            etapa="Presentación de consultas y observaciones",
            fecha_inicio="10/05/2026 09:00",
            fecha_fin="15/05/2026 23:59",
        ),
        CronogramaEtapa(
            etapa="Presentación de propuestas",
            fecha_inicio="20/05/2026 09:00",
            fecha_fin="27/05/2026 15:00",
        ),
    ]
    fechas = extract_cronograma_fechas(cronograma)
    assert fechas.fecha_consultas == "15/05/2026 23:59"
    assert fechas.fecha_presentacion == "27/05/2026 15:00"


def test_extract_cronograma_fechas_fin_fallback_to_inicio():
    cronograma = [
        CronogramaEtapa(
            etapa="Presentación de propuestas",
            fecha_inicio="20/05/2026 09:00",
            fecha_fin="",
        ),
    ]
    fechas = extract_cronograma_fechas(cronograma)
    assert fechas.fecha_presentacion == "20/05/2026 09:00"
