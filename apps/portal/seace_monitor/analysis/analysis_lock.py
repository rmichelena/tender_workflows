"""Lock por proceso para serializar análisis Gemini y chat."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


class AnalysisBusyError(RuntimeError):
    """Otro análisis o chat está en curso para este proceso."""


@contextmanager
def analysis_lock(proc_dir: Path, *, blocking: bool = True):
    """Exclusión mutua en `{proc_dir}/fast_analysis/.analysis.lock`."""
    lock_dir = proc_dir / "fast_analysis"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".analysis.lock"
    handle = lock_path.open("a+")
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            raise AnalysisBusyError(
                "Análisis o chat en curso para este proceso. Espera e intenta de nuevo."
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
