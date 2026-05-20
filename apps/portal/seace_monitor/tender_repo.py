"""Resolución de la raíz del monorepo tender_workflows."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_tender_repo_root() -> Path:
    """Raíz del repo (contiene instrucciones/ y scripts/run_step1_to_1_3.py)."""
    env = os.environ.get("TENDER_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (
            (parent / "instrucciones" / "01_workflow.md").is_file()
            and (parent / "scripts" / "run_step1_to_1_3.py").is_file()
        ):
            return parent

    raise RuntimeError(
        "No se encontró la raíz de tender_workflows. "
        "Define TENDER_REPO_ROOT o ejecuta desde el monorepo."
    )
