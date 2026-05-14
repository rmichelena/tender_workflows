#!/usr/bin/env python3
"""
Google Document AI — Layout Parser (Online/Chunked mode).

Extrae texto + estructura de PDFs usando Google Document AI Layout Parser.
Sin dependencia de Google Cloud Storage — funciona con OAuth de usuario.

Modo ONLINE: documentos ≤ online_page_limit páginas (15 por defecto).
Modo CHUNKED: documentos grandes se dividen en trozos y se procesan secuencialmente.

⚠️ LIMITACIÓN: El modo chunked parte el documento arbitrariamente, lo que puede
perder estructura/semántica entre páginas. Para documentos grandes, preferir
el modo batch con GCS (ver docai_batch_gcs.py).

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

import sys, os, time, json, base64, tempfile

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
    doc = fitz.open(file_path)
    n = len(doc)
    doc.close()
    return n


def split_pdf(file_path, chunk_size):
    """Divide PDF en trozos de N páginas. Retorna lista de (temp_path, start, end).
    
    Cleans up temp files if an exception occurs mid-split.
    """
    import fitz
    doc = fitz.open(file_path)
    total = len(doc)
    chunks = []
    try:
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp_path = tmp.name
            tmp.close()
            new_doc = fitz.open()
            try:
                for pg in range(start, end):
                    new_doc.insert_pdf(doc, from_page=pg, to_page=pg)
                new_doc.save(tmp_path)
            finally:
                new_doc.close()
            chunks.append((tmp_path, start, end))
    except Exception:
        # Clean up any temp files created before the failure
        for tmp_path, _, _ in chunks:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        raise
    finally:
        doc.close()
    return chunks


def process_single_chunk(pdf_bytes, creds):
    """Envía un chunk al API online de DocAI."""
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

    # creds_getter for token refresh mid-chunking (same pattern as docai_batch_gcs)
    _creds = [creds]
    def creds_getter(force_refresh=False):
        if force_refresh or not _creds[0].valid:
            from google.auth.transport.requests import Request
            _creds[0].refresh(Request())
        return _creds[0]

    pages = count_pdf_pages(input_file)
    print(f"Pages: {pages}")

    all_chunks, all_blocks, failed_chunks = [], [], []
    page_limit = cfg["online_page_limit"]

    if pages <= page_limit:
        print(f"Mode: ONLINE")
        with open(input_file, "rb") as f:
            pdf_bytes = f.read()
        result = process_single_chunk(pdf_bytes, creds_getter())
        chunks, blocks = extract_from_response(result)
        all_chunks.extend(chunks)
        all_blocks.extend(blocks)
    else:
        print(f"Mode: CHUNKED ({pages} pages in chunks of {page_limit})")
        pdf_chunks = split_pdf(input_file, page_limit)
        try:
            for i, (chunk_path, start_pg, end_pg) in enumerate(pdf_chunks):
                print(f"  Chunk {i+1}/{len(pdf_chunks)}: pages {start_pg+1}-{end_pg}...", end=" ", flush=True)
                with open(chunk_path, "rb") as f:
                    chunk_bytes = f.read()
                try:
                    result = process_single_chunk(chunk_bytes, creds_getter())
                    chunks, blocks = extract_from_response(result, page_offset=start_pg)
                    all_chunks.extend(chunks)
                    all_blocks.extend(blocks)
                    print(f"OK ({len(chunks)} chunks)")
                except Exception as e:
                    from common import DocAIError
                    # On 401 (auth), force refresh and retry once
                    if isinstance(e, DocAIError) and e.is_auth:
                        try:
                            result = process_single_chunk(chunk_bytes, creds_getter(force_refresh=True))
                            chunks, blocks = extract_from_response(result, page_offset=start_pg)
                            all_chunks.extend(chunks)
                            all_blocks.extend(blocks)
                            print(f"OK after refresh ({len(chunks)} chunks)")
                        except Exception as e2:
                            failed_chunks.append({"pages": f"{start_pg+1}-{end_pg}", "error": str(e2)})
                            print(f"ERROR after retry: {e2}")
                    elif isinstance(e, DocAIError) and e.is_permission:
                        failed_chunks.append({"pages": f"{start_pg+1}-{end_pg}", "error": f"PERMISSION DENIED: {e}"})
                        print(f"PERMISSION ERROR: {e}")
                    else:
                        failed_chunks.append({"pages": f"{start_pg+1}-{end_pg}", "error": str(e)})
                        print(f"ERROR: {e}")
                finally:
                    if os.path.exists(chunk_path):
                        os.unlink(chunk_path)
        finally:
            # Ensure ALL temp files are cleaned up (covers split_pdf partial failure)
            for chunk_path, _, _ in pdf_chunks:
                if os.path.exists(chunk_path):
                    os.unlink(chunk_path)

    md = build_markdown(all_chunks,
                        filter_headers_footers=cfg.get("filter_headers_footers", True),
                        filter_image_annotations=not cfg.get("enable_image_annotation", False))
    elapsed = time.time() - t0

    text_blocks = sum(1 for b in all_blocks if b.get("type") == "text")
    table_blocks = sum(1 for b in all_blocks if b.get("type") == "table")

    print(f"\nResults: {elapsed:.1f}s | {len(all_chunks)} chunks | {len(all_blocks)} blocks | {len(md):,} chars")
    if failed_chunks:
        print(f"WARNING: {len(failed_chunks)} chunks failed!")

    md_path = os.path.join(output_dir, f"{base}_docai.md")
    json_path = os.path.join(output_dir, f"{base}_docai.json")
    with open(md_path, "w") as f:
        f.write(md)
    with open(json_path, "w") as f:
        json.dump({
            "source": input_file, "extractor": "google_docai_online",
            "pages": pages, "elapsed_seconds": round(elapsed, 1),
            "markdown_length": len(md), "chunks": all_chunks, "layout_blocks": all_blocks,
            "failed_chunks": failed_chunks,
            "metrics": {"total_chunks": len(all_chunks), "total_blocks": len(all_blocks),
                        "text_blocks": text_blocks, "table_blocks": table_blocks,
                        "failed_chunks": len(failed_chunks)}
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved: {md_path}, {json_path}")

    if failed_chunks:
        sys.exit(1)


if __name__ == "__main__":
    main()
