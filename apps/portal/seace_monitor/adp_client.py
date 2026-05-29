"""Cliente HTTP para el portal de Aeropuertos del Perú (ADP).

Encapsula el acceso al endpoint ``get_partial_view_competition``
y la descarga de documentos PDF.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from .http_util import requests_proxies

logger = logging.getLogger(__name__)

ADP_BASE_URL = "https://www.adp.com.pe"
ADP_PARTIAL_VIEW_URL = (
    f"{ADP_BASE_URL}/web/get_partial_view_competition"
)
ADP_DOWNLOAD_URL = (
    f"{ADP_BASE_URL}/Web/getFile_store_competition_download"
)

# work_id → categoría
WORK_CATEGORIES: dict[int, str] = {
    3: "bienes",
    4: "servicios",
    1: "consultorias",
    2: "obras",
}

ALL_WORK_IDS = list(WORK_CATEGORIES.keys())

_FETCH_TIMEOUT = 60  # HTML ~1MB
_DOWNLOAD_TIMEOUT = 120
_RETRIES = 3
_RETRY_BACKOFF = (1.0, 2.0, 4.0)


class AdpClient:
    """Cliente HTTP para el portal ADP."""

    def __init__(self, http_proxy: str | None = None) -> None:
        self.session = requests.Session()
        self.proxies = requests_proxies(http_proxy)
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*",
            }
        )

    def fetch_category_html(self, work_id: int) -> str:
        """Obtiene el HTML de una categoría de procesos con reintentos.

        Args:
            work_id: 1=consultorías, 2=obras, 3=bienes, 4=servicios.

        Returns:
            HTML parcial con la lista de procesos.

        Raises:
            requests.RequestException: Error de red agotados reintentos.
            ValueError: ``work_id`` inválido.
        """
        if work_id not in WORK_CATEGORIES:
            raise ValueError(f"work_id inválido: {work_id!r}")
        params = {"lang": "es", "site_id": "", "work_id": str(work_id)}
        last_error: Exception | None = None

        for attempt in range(_RETRIES):
            try:
                r = self.session.get(
                    ADP_PARTIAL_VIEW_URL,
                    params=params,
                    timeout=_FETCH_TIMEOUT,
                    proxies=self.proxies,
                )
                r.raise_for_status()
                cat = WORK_CATEGORIES[work_id]
                logger.debug(
                    "ADP fetch work_id=%s (%s): %s bytes",
                    work_id,
                    cat,
                    len(r.text),
                )
                return r.text
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                if attempt < _RETRIES - 1:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "ADP fetch work_id=%s intento %s/%s falló: %s — reintentando en %.1fs",
                        work_id,
                        attempt + 1,
                        _RETRIES,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)

        assert last_error is not None
        raise last_error

    def download_document(
        self,
        name_file: str,
        dest: Path,
    ) -> Path:
        """Descarga un documento PDF del portal ADP.

        Args:
            name_file: Nombre hash del archivo en el servidor.
            dest: Ruta destino en disco.

        Returns:
            Ruta del archivo descargado.

        Raises:
            RuntimeError: Descarga vacía o reintentos agotados.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None

        for attempt in range(_RETRIES):
            try:
                return self._download_once(name_file, dest)
            except (requests.RequestException, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt < _RETRIES - 1:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "ADP download intent %s/%s falló para %s: %s — reintentando en %.1fs",
                        attempt + 1,
                        _RETRIES,
                        name_file,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)

        assert last_error is not None
        raise last_error

    def _download_once(self, name_file: str, dest: Path) -> Path:
        params = {"name_file": name_file, "new_name": name_file}
        r = self.session.get(
            ADP_DOWNLOAD_URL,
            params=params,
            timeout=_DOWNLOAD_TIMEOUT,
            proxies=self.proxies,
            stream=True,
        )
        r.raise_for_status()

        part = dest.with_name(f"{dest.name}.part")
        if part.exists():
            part.unlink()
        written = 0
        try:
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
        except Exception:
            part.unlink(missing_ok=True)
            raise

        if written == 0:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"Descarga vacía para documento ADP {name_file}")

        part.replace(dest)
        return dest

    def close(self) -> None:
        self.session.close()
