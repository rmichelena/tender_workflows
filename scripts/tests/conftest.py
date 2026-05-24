"""Fixtures compartidas para tests de scripts/ etapa C."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
EXTRACTORS = SCRIPTS / "extractors"

for path in (SCRIPTS, EXTRACTORS):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


@pytest.fixture
def repo_root() -> Path:
    return REPO


@pytest.fixture
def make_min_pdf():
    import fitz

    def _make(path: Path, *, pages: int = 1) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
        doc.close()
        return path

    return _make
