"""Utilidades compartidas para cliente Gemini (upload, reintentos)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_GEMINI_RETRY_ATTEMPTS = 5
_GEMINI_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_LIMA_TZ = ZoneInfo("America/Lima")
_MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def today_anchor_peru() -> tuple[str, int, str]:
    now = datetime.now(_LIMA_TZ)
    mes = _MESES_ES[now.month - 1]
    human = f"{now.day} de {mes} de {now.year}"
    return human, now.year, now.strftime("%Y-%m-%d")


def get_genai_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def is_retryable_gemini_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in _GEMINI_RETRYABLE_STATUS:
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "service unavailable",
            "upload has already been terminated",
            "resource exhausted",
            "deadline exceeded",
            "internal error",
        )
    )


def gemini_retry_delay(attempt: int) -> float:
    return min(2**attempt, 30)


def _file_state_name(file) -> str | None:
    state = getattr(file, "state", None)
    return getattr(state, "name", None) if state else None


def wait_file_active(client, uploaded) -> Any:
    file = uploaded
    state_name = _file_state_name(file)
    if state_name == "ACTIVE":
        return file
    if state_name == "FAILED":
        raise RuntimeError(f"Gemini rechazó el procesamiento del archivo: {file.name}")
    if state_name not in (None, "PROCESSING"):
        raise RuntimeError(f"Estado inesperado del archivo Gemini: {state_name}")

    deadline = time.time() + 300
    while True:
        if time.time() > deadline:
            raise RuntimeError("Timeout esperando procesamiento del archivo en Gemini")
        time.sleep(2)
        file = client.files.get(name=file.name)
        state_name = _file_state_name(file)
        if state_name == "ACTIVE":
            return file
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini rechazó el procesamiento del archivo: {file.name}")
        if state_name not in (None, "PROCESSING"):
            raise RuntimeError(f"Estado inesperado del archivo Gemini: {state_name}")


def upload_file_with_retry(api_key: str, path: Path):
    last_exc: Exception | None = None
    for attempt in range(1, _GEMINI_RETRY_ATTEMPTS + 1):
        client = get_genai_client(api_key)
        uploaded = None
        try:
            uploaded = client.files.upload(file=str(path))
            return wait_file_active(client, uploaded)
        except Exception as exc:
            if uploaded is not None:
                delete_remote_files(client, [uploaded])
            last_exc = exc
            if attempt >= _GEMINI_RETRY_ATTEMPTS or not is_retryable_gemini_error(exc):
                raise
            delay = gemini_retry_delay(attempt)
            logger.warning(
                "Reintento %s/%s subida a Gemini (%s): %s; espera %ss",
                attempt,
                _GEMINI_RETRY_ATTEMPTS,
                path.name,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"No se pudo subir {path.name} a Gemini")


def generate_content_with_retry(client, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, _GEMINI_RETRY_ATTEMPTS + 1):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= _GEMINI_RETRY_ATTEMPTS or not is_retryable_gemini_error(exc):
                raise
            delay = gemini_retry_delay(attempt)
            logger.warning(
                "Reintento %s/%s generateContent Gemini: %s; espera %ss",
                attempt,
                _GEMINI_RETRY_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini generateContent falló tras reintentos")


def delete_remote_files(
    client, uploaded_files, *, proc_dir: Path | None = None
) -> list[str]:
    """Borra uploads remotos; retorna nombres que no se pudieron eliminar."""
    failed: list[str] = []
    for uploaded in uploaded_files:
        name = getattr(uploaded, "name", str(uploaded))
        try:
            client.files.delete(name=name)
        except Exception as exc:
            logger.warning("No se pudo borrar archivo temporal en Gemini: %s", name)
            failed.append(name)
            if proc_dir is not None:
                from .gemini_orphans import log_gemini_orphan

                log_gemini_orphan(
                    proc_dir, kind="file", resource_id=name, error=str(exc)
                )
    return failed
