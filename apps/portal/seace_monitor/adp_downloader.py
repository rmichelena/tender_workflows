"""Descarga y almacenamiento de documentos del portal ADP.

Guarda cada PDF en el directorio del proceso y actualiza ``documentos_json``
con la ruta en disco.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .adp_client import AdpClient
from .document_storage import sanitize_download_filename

logger = logging.getLogger(__name__)


def download_adp_documents(
    docs_dir: Path,
    documentos: list[dict],
    client: AdpClient,
) -> int:
    """Descarga documentos ADP que aún no están en disco.

    Args:
        docs_dir: Directorio ``documentos/`` del proceso.
        documentos: Lista de diccionarios (desde ``documentos_json``).
            Se muta in-place para añadir ``archivo``.
        client: Cliente ADP configurado.

    Returns:
        Número de documentos descargados.
    """
    docs_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0

    for doc in documentos:
        name_file = doc.get("name_file", "")
        if not name_file:
            continue
        archivo = doc.get("archivo", "").strip()
        if archivo and (docs_dir / archivo).exists():
            continue

        friendly = doc.get("new_name") or doc.get("title") or name_file
        if not friendly.lower().endswith(".pdf"):
            friendly += ".pdf"
        filename = sanitize_download_filename(friendly)
        dest = docs_dir / filename

        if dest.exists():
            doc["archivo"] = filename
            continue

        try:
            client.download_document(name_file, dest)
            doc["archivo"] = filename
            downloaded += 1
            logger.info("ADP descargado: %s → %s", name_file, filename)
        except Exception:
            logger.exception("ADP: fallo descargando %s", name_file)

    return downloaded
