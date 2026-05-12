#!/usr/bin/env python3
"""
Google Document AI — Layout Parser (Batch mode via GCS).

Extrae texto + estructura de PDFs usando el modo batch del API.
Requiere bucket de Google Cloud Storage para input/output.
Es el método RECOMENDADO para documentos >15 páginas porque:
  - No fragmenta el documento — procesa todo como unidad
  - Preserva la semántica y estructura entre páginas
  - Sin problemas de chunk size o timeout por chunk

Flujo:
  1. Upload PDF a gs://{BUCKET}/input/{run_id}/
  2. Llama a batchProcess API (async, hasta 500 páginas)
  3. Poll de la operación hasta completar (con token refresh)
  4. Descarga resultados desde gs://{BUCKET}/output/{run_id}/
  5. Limpia archivos temporales de GCS (en finally block)

Salida:
  - {nombre}_docai_batch.md   — Markdown con texto completo
  - {nombre}_docai_batch.json — JSON estructurado con chunks + layout blocks

Uso:
  python3 docai_batch_gcs.py <input.pdf> [output_dir]

Dependencias:
  pip install requests google-auth-oauthlib

Configuración:
  Ver extractors.conf.example
"""

import sys, os, time, json, re, urllib.parse, uuid

from common import (
    get_docai_config, get_creds, sanitize_filename,
    parse_chunks, parse_layout_blocks, build_markdown,
    get_processor_endpoint, classify_http_error, retry_request, DocAIError,
)
import requests

cfg = get_docai_config()
GCS_BUCKET = cfg["gcs_bucket"]
BATCH_ENDPOINT = get_processor_endpoint(cfg).replace(":process", ":batchProcess")
API_BASE = f"https://{cfg['location']}-documentai.googleapis.com"


# ─── GCS helpers ──────────────────────────────────────────

def gcs_upload(file_path, gcs_path, creds, content_type="application/pdf"):
    """Upload archivo local a GCS via JSON API (streaming)."""
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
    file_size = os.path.getsize(file_path)
    with open(file_path, "rb") as f:
        r = requests.post(url,
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": content_type},
            params={"uploadType": "media", "name": gcs_path},
            data=f, timeout=120)
    if r.status_code not in (200, 201):
        raise Exception(f"GCS upload error {r.status_code}: {r.text[:500]}")
    print(f"  Uploaded: gs://{GCS_BUCKET}/{gcs_path} ({file_size/1024:.0f} KB)")
    return r.json()


def gcs_download(gcs_path, creds):
    """Descarga archivo de GCS via JSON API."""
    encoded = urllib.parse.quote(gcs_path, safe="")
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}?alt=media"
    r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=120)
    if r.status_code != 200:
        raise Exception(f"GCS download error {r.status_code}: {r.text[:500]}")
    return r.content


def gcs_list(prefix, creds):
    """Lista objetos en GCS con un prefijo (handles pagination)."""
    all_items = []
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o"
    params = {"prefix": prefix, "maxResults": 100}
    while True:
        r = requests.get(url,
            headers={"Authorization": f"Bearer {creds.token}"},
            params=params, timeout=30)
        if r.status_code != 200:
            raise Exception(f"GCS list error {r.status_code}: {r.text[:500]}")
        data = r.json()
        all_items.extend(data.get("items", []))
        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
    return all_items


def gcs_delete(gcs_path, creds):
    """Borra un objeto de GCS."""
    encoded = urllib.parse.quote(gcs_path, safe="")
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}"
    r = requests.delete(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
    return r.status_code == 204


def gcs_cleanup(gcs_input_path, gcs_output_prefix, output_files, creds):
    """Clean up GCS input and output files. Safe to call multiple times.
    
    If output_files is empty (e.g. gcs_list failed), enumerates by prefix
    to avoid orphaning output objects in GCS.
    """
    errors = []
    # Always delete input
    try:
        gcs_delete(gcs_input_path, creds)
    except Exception as e:
        errors.append(f"input: {e}")
    
    # Delete output files: use provided list, or enumerate by prefix if empty
    if output_files:
        for obj in output_files:
            try:
                gcs_delete(obj["name"], creds)
            except Exception as e:
                errors.append(f"output/{obj['name']}: {e}")
    elif gcs_output_prefix:
        # gcs_list failed earlier — enumerate outputs now to prevent orphans
        try:
            found = gcs_list(gcs_output_prefix, creds)
            for obj in found:
                try:
                    gcs_delete(obj["name"], creds)
                except Exception as e:
                    errors.append(f"output/{obj['name']}: {e}")
            if found:
                print(f"  Cleaned {len(found)} orphaned output objects via prefix enumeration")
        except Exception as e:
            errors.append(f"output-prefix-enumeration: {e}")
            print(f"  WARNING: Could not enumerate outputs for cleanup: {e}")
    
    if errors:
        print(f"  Cleanup warnings: {errors}")


# ─── DocAI Batch ──────────────────────────────────────────

def start_batch_process(gcs_input_uri, gcs_output_uri, creds):
    """Inicia un job batch de Document AI."""
    body = {
        "inputDocuments": {
            "gcsDocuments": {
                "documents": [{"gcsUri": gcs_input_uri, "mimeType": "application/pdf"}]
            }
        },
        "documentOutputConfig": {
            "gcsOutputConfig": {"gcsUri": gcs_output_uri}
        },
        "processOptions": {
            "layoutConfig": {
                "enableTableAnnotation": True,
                "enableImageAnnotation": cfg.get("enable_image_annotation", False),
                "chunkingConfig": {"chunkSize": cfg["chunk_size"], "includeAncestorHeadings": True}
            }
        }
    }
    url = f"{API_BASE}{BATCH_ENDPOINT}"

    def _do_request():
        r = requests.post(url,
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
            json=body, timeout=60)
        if r.status_code != 200:
            raise classify_http_error(r.status_code, r.text)
        return r.json()

    result = retry_request(_do_request)
    op_name = result.get("name", "")
    print(f"  Operation: {op_name}")
    return op_name


def poll_operation(operation_name, creds_getter):
    """Poll de operación long-running hasta completar.

    creds_getter: callable returning fresh credentials (handles token refresh).
    Handles 401 (auth refresh), 429/5xx (retry with backoff), 403 (permission error).
    """
    url = f"{API_BASE}/v1/{operation_name}"
    start = time.time()
    while True:
        creds = creds_getter()

        def _do_poll():
            r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
            if r.status_code == 401:
                raise classify_http_error(401, r.text)
            if r.status_code == 403:
                raise classify_http_error(403, r.text)
            if r.status_code != 200:
                raise classify_http_error(r.status_code, r.text)
            return r

        try:
            r = retry_request(_do_poll, max_retries=2)
        except DocAIError as e:
            if e.is_auth:
                # Token expired mid-poll — force refresh with retry
                for attempt in range(3):
                    try:
                        creds = creds_getter(force_refresh=True)
                        break
                    except Exception as refresh_err:
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                        else:
                            raise Exception(f"Token refresh failed after 3 attempts: {refresh_err}")
                continue  # retry poll with fresh creds
            elif e.is_permission:
                raise Exception(f"PERMISSION DENIED: Check IAM roles for service agent. {e}")
            raise

        result = r.json()
        if result.get("done"):
            error = result.get("error")
            if error:
                raise Exception(f"Operation failed: {error}")
            print(f"  Completed in {time.time()-start:.0f}s")
            return result
        elapsed = time.time() - start
        print(f"  Polling... ({elapsed:.0f}s)", flush=True)
        if elapsed > cfg["max_wait"]:
            raise Exception(f"Timeout after {cfg['max_wait']}s")
        time.sleep(cfg["poll_interval"])


def extract_from_batch(output_files, creds):
    """Descarga y extrae markdown de los resultados batch."""
    all_chunks, all_blocks = [], []
    for obj in output_files:
        gcs_path = obj["name"]
        if not gcs_path.endswith(".json"):
            continue
        print(f"  Downloading: {gcs_path}")
        content = gcs_download(gcs_path, creds)
        result = json.loads(content)
        doc_data = result.get("document", result)

        all_chunks.extend(parse_chunks(
            doc_data.get("chunkedDocument", {}).get("chunks", [])
        ))
        all_blocks.extend(parse_layout_blocks(
            doc_data.get("documentLayout", {}).get("blocks", [])
        ))

    md = build_markdown(all_chunks,
                        filter_headers_footers=cfg.get("filter_headers_footers", True),
                        filter_image_annotations=not cfg.get("enable_image_annotation", False))
    return md, all_chunks, all_blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 docai_batch_gcs.py <input.pdf> [output_dir]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_file)
    os.makedirs(output_dir, exist_ok=True)
    base = sanitize_filename(input_file)

    run_id = uuid.uuid4().hex[:8]
    gcs_input_path = f"input/{run_id}/{base}.pdf"
    gcs_output_prefix = f"output/{run_id}/"
    output_files = []

    print(f"Processing (BATCH/GCS): {input_file}")
    print(f"Run ID: {run_id}")
    t0 = time.time()
    creds = get_creds(cfg["token_path"])

    # creds_getter for poll_operation (supports token refresh mid-poll)
    _creds = [creds]
    def creds_getter(force_refresh=False):
        if force_refresh or _creds[0].expired:
            from google.auth.transport.requests import Request
            _creds[0].refresh(Request())
        return _creds[0]

    try:
        print("\n[1/4] Uploading to GCS...")
        gcs_upload(input_file, gcs_input_path, creds_getter())

        print("\n[2/4] Starting batch process...")
        op_name = start_batch_process(
            f"gs://{GCS_BUCKET}/{gcs_input_path}",
            f"gs://{GCS_BUCKET}/{gcs_output_prefix}", creds_getter())

        print("\n[3/4] Waiting for completion...")
        poll_operation(op_name, creds_getter)

        print("\n[4/4] Downloading results...")
        output_files = gcs_list(gcs_output_prefix, creds_getter())
        print(f"  Found {len(output_files)} output files")
        md, all_chunks, all_blocks = extract_from_batch(output_files, creds_getter())

        elapsed = time.time() - t0
        text_blocks = sum(1 for b in all_blocks if b.get("type") == "text")
        table_blocks = sum(1 for b in all_blocks if b.get("type") == "table")

        print(f"\nResults: {elapsed:.1f}s ({elapsed/60:.1f} min) | {len(all_chunks)} chunks | {len(all_blocks)} blocks | {len(md):,} chars")

        md_path = os.path.join(output_dir, f"{base}_docai_batch.md")
        json_path = os.path.join(output_dir, f"{base}_docai_batch.json")
        with open(md_path, "w") as f:
            f.write(md)
        with open(json_path, "w") as f:
            json.dump({
                "source": input_file, "extractor": "google_docai_batch_gcs",
                "gcs_bucket": GCS_BUCKET, "run_id": run_id, "operation": op_name,
                "elapsed_seconds": round(elapsed, 1), "markdown_length": len(md),
                "chunks": all_chunks, "layout_blocks": all_blocks,
                "metrics": {"total_chunks": len(all_chunks), "total_blocks": len(all_blocks),
                            "text_blocks": text_blocks, "table_blocks": table_blocks}
            }, f, indent=2, ensure_ascii=False)
        print(f"Saved: {md_path}, {json_path}")

    finally:
        print("\nCleaning up GCS...")
        gcs_cleanup(gcs_input_path, gcs_output_prefix, output_files, creds_getter())
        print("  Done.")


if __name__ == "__main__":
    main()
