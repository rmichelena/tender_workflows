#!/usr/bin/env python3
"""
Modal Docling Serve extractor — same API as docling_extract.py, but defaults to
Roberto's Modal-hosted Docling service and async mode.

Guide:
  scripts/extractors/api guide references/docling-modal-api-guide.md

Usage:
  python3 modal_docling_extract.py <input_file> [output]
  python3 modal_docling_extract.py <input_file> --output-dir <dir>
  python3 modal_docling_extract.py <input_file> out.md          # async by default
  python3 modal_docling_extract.py <input_file> out.json --json # raw result

Standard --output-dir outputs:
  {basename}_modal_docling.md and {basename}_modal_docling.json
"""

from docling_extract import run

MODAL_DOCLING_BASE = "https://rmichelena--docling-converter-fastapi-app.modal.run"


if __name__ == "__main__":
    raise SystemExit(
        run(
            default_base_url=MODAL_DOCLING_BASE,
            default_suffix="modal_docling",
            default_async=True,
            config_name="modal_docling",
        )
    )
