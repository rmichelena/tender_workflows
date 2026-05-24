#!/usr/bin/env python3
"""
Google Document AI — Layout Parser (online mode only).

Extrae texto + estructura de PDFs usando Google Document AI Layout Parser.
Sin dependencia de Google Cloud Storage — funciona con OAuth de usuario.

Modo ONLINE: documentos ≤ online_page_limit páginas (15 por defecto).
Para documentos más grandes usar docai_batch_gcs.py (batch con GCS).

Salida:
  - {nombre}_docai.md   — Markdown con texto completo
  - {nombre}_docai.json — JSON estructurado con chunks + layout blocks

Uso:
  python3 docai_online.py <input.pdf> [output_dir]

Dependencias:
  pip install requests PyMuPDF google-auth-oauthlib

Configuración:
  Ver extractors.conf.example
"""

import sys, os, time, json, base64

from common import (
    get_docai_config, get_creds, sanitize_filename,
    parse_chunks, parse_layout_blocks, build_markdown,
    get_processor_endpoint, classify_http_error, retry_request,
)
import requests

cfg = get_docai_config()
PROCESS_ENDPOINT = get_processor_endpoint(cfg)
API_BASE = f"https://{cfg['location']}-documentai.googleapis.com"


def count_pdf_pages(file_path):
    """Cuenta páginas de un PDF con PyMuPDF."""
    import fitz
    with fitz.open(file_path) as doc:
        return len(doc)


def process_single_chunk(pdf_bytes, creds):
    """Envía un documento al API online de DocAI."""
    body = {
        "rawDocument": {
            "mimeType": "application/pdf",
            "content": base64.b64encode(pdf_bytes).decode()
        },
        "processOptions": {
            "layoutConfig": {
                "enableTableAnnotation": True,
                "enableImageAnnotation": cfg.get("enable_image_annotation", False),
                "chunkingConfig": {
                    "chunkSize": cfg["chunk_size"],
                    "includeAncestorHeadings": False
                }
            }
        }
    }
    url = f"{API_BASE}{PROCESS_ENDPOINT}"

    def _do_request():
        r = requests.post(url,
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
            json=body, timeout=600)
        if r.status_code != 200:
            raise classify_http_error(r.status_code, r.text)
        return r.json()

    return retry_request(_do_request)


def extract_from_response(result, page_offset=0):
    """Extrae chunks y blocks de una respuesta DocAI."""
    doc = result.get("document", {})
    chunks = parse_chunks(
        doc.get("chunkedDocument", {}).get("chunks", []),
        page_offset=page_offset
    )
    blocks = parse_layout_blocks(
        doc.get("documentLayout", {}).get("blocks", []),
        page_offset=page_offset
    )
    return chunks, blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 docai_online.py <input.pdf> [output_dir]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_file)
    os.makedirs(output_dir, exist_ok=True)
    base = sanitize_filename(input_file)

    print(f"Processing: {input_file}")
    t0 = time.time()
    creds = get_creds(cfg["token_path"])

    pages = count_pdf_pages(input_file)
    print(f"Pages: {pages}")

    page_limit = cfg["online_page_limit"]
    if pages > page_limit:
        print(
            f"ERROR: {pages} pages exceeds online limit ({page_limit}). "
            f"Use docai_batch_gcs.py for larger documents.",
            file=sys.stderr,
        )
        sys.exit(2)

    print("Mode: ONLINE")
    with open(input_file, "rb") as f:
        pdf_bytes = f.read()
    result = process_single_chunk(pdf_bytes, creds)
    all_chunks, all_blocks = extract_from_response(result)

    md = build_markdown(all_chunks,
                        filter_headers_footers=cfg.get("filter_headers_footers", True),
                        filter_image_annotations=not cfg.get("enable_image_annotation", False))
    elapsed = time.time() - t0

    text_blocks = sum(1 for b in all_blocks if b.get("type") == "text")
    table_blocks = sum(1 for b in all_blocks if b.get("type") == "table")

    print(f"\nResults: {elapsed:.1f}s | {len(all_chunks)} chunks | {len(all_blocks)} blocks | {len(md):,} chars")

    md_path = os.path.join(output_dir, f"{base}_docai.md")
    json_path = os.path.join(output_dir, f"{base}_docai.json")
    with open(md_path, "w") as f:
        f.write(md)
    with open(json_path, "w") as f:
        json.dump({
            "source": input_file, "extractor": "google_docai_online",
            "pages": pages, "elapsed_seconds": round(elapsed, 1),
            "markdown_length": len(md), "chunks": all_chunks, "layout_blocks": all_blocks,
            "metrics": {"total_chunks": len(all_chunks), "total_blocks": len(all_blocks),
                        "text_blocks": text_blocks, "table_blocks": table_blocks},
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved: {md_path}, {json_path}")


if __name__ == "__main__":
    main()
