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
  1. Upload PDF a gs://{BUCKET}/input/
  2. Llama a batchProcess API (async, hasta 500 páginas)
  3. Poll de la operación hasta completar
  4. Descarga resultados desde gs://{BUCKET}/output/
  5. Limpia archivos temporales de GCS

Salida:
  - {nombre}_docai_batch.md   — Markdown con texto completo
  - {nombre}_docai_batch.json — JSON estructurado con chunks + layout blocks

Uso:
  python3 docai_batch_gcs.py <input.pdf> [output_dir]

Dependencias:
  pip install requests PyMuPDF google-auth-oauthlib

Setup requerido (una vez):
  1. Crear bucket GCS (ej: gs://hermoberto)
  2. Crear el service agent de DocAI:
     POST https://serviceusage.googleapis.com/v1beta1/projects/{PROJECT_NUMBER}/services/documentai.googleapis.com:generateServiceIdentity
  3. Otorgar roles/storage.objectViewer + roles/storage.objectCreator al service agent en el bucket

Configuración:
  - Credenciales OAuth en /opt/data/google_token_personal.json
  - BUCKET, PROJECT_ID, PROCESSOR_ID en la sección Config
"""

import sys, os, time, json, re, tempfile, urllib.parse

sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ─── Config ───────────────────────────────────────────────
PROJECT_ID = "839375208239"
LOCATION = "us"
PROCESSOR_ID = "b8ea939312a8ff4"
PROCESSOR_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
API_BASE = f"https://{LOCATION}-documentai.googleapis.com"
GCS_BUCKET = "hermoberto"

TOKEN_PATH = "/opt/data/google_token_personal.json"
POLL_INTERVAL = 20  # segundos entre polls
MAX_WAIT = 3600     # timeout máximo en segundos


def get_creds():
    with open(TOKEN_PATH) as f:
        creds = Credentials.from_authorized_user_info(json.load(f))
    if creds.expired:
        creds.refresh(Request())
    return creds


# ─── GCS helpers ──────────────────────────────────────────

def gcs_upload(file_path, gcs_path, creds, content_type="application/pdf"):
    """Upload archivo local a GCS via JSON API."""
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
    with open(file_path, "rb") as f:
        data = f.read()
    r = requests.post(url,
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": content_type},
        params={"uploadType": "media", "name": gcs_path},
        data=data, timeout=120)
    if r.status_code not in (200, 201):
        raise Exception(f"GCS upload error {r.status_code}: {r.text[:500]}")
    print(f"  Uploaded: gs://{GCS_BUCKET}/{gcs_path} ({len(data)/1024:.0f} KB)")
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
    """Lista objetos en GCS con un prefijo."""
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o"
    r = requests.get(url,
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"prefix": prefix, "maxResults": 100}, timeout=30)
    if r.status_code != 200:
        raise Exception(f"GCS list error {r.status_code}: {r.text[:500]}")
    return r.json().get("items", [])


def gcs_delete(gcs_path, creds):
    """Borra un objeto de GCS."""
    encoded = urllib.parse.quote(gcs_path, safe="")
    url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}"
    r = requests.delete(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
    return r.status_code == 204


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
                "enableImageAnnotation": True,
                "chunkingConfig": {"chunkSize": 500, "includeAncestorHeadings": True}
            }
        }
    }
    url = f"{API_BASE}/v1/{PROCESSOR_NAME}:batchProcess"
    r = requests.post(url,
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=body, timeout=60)
    if r.status_code != 200:
        raise Exception(f"batchProcess error {r.status_code}: {r.text[:500]}")
    op_name = r.json().get("name", "")
    print(f"  Operation: {op_name}")
    return op_name


def poll_operation(operation_name, creds):
    """Poll de operación long-running hasta completar."""
    url = f"{API_BASE}/v1/{operation_name}"
    start = time.time()
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
        if r.status_code != 200:
            raise Exception(f"Poll error {r.status_code}: {r.text[:300]}")
        result = r.json()
        if result.get("done"):
            error = result.get("error")
            if error:
                raise Exception(f"Operation failed: {error}")
            print(f"  Completed in {time.time()-start:.0f}s")
            return result
        elapsed = time.time() - start
        print(f"  Polling... ({elapsed:.0f}s)", flush=True)
        if elapsed > MAX_WAIT:
            raise Exception(f"Timeout after {MAX_WAIT}s")
        time.sleep(POLL_INTERVAL)


def extract_from_batch(output_files, creds):
    """Descarga y extrae markdown de los resultados batch."""
    all_chunk_texts, all_chunks, all_blocks = [], [], []
    for obj in output_files:
        gcs_path = obj["name"]
        if not gcs_path.endswith(".json"):
            continue
        print(f"  Downloading: {gcs_path}")
        content = gcs_download(gcs_path, creds)
        result = json.loads(content)
        doc_data = result.get("document", result)

        for c in doc_data.get("chunkedDocument", {}).get("chunks", []):
            all_chunks.append({
                "chunk_id": c.get("chunkId"),
                "content": c.get("content", ""),
                "page_span": c.get("pageSpan", {}),
            })

        for b in doc_data.get("documentLayout", {}).get("blocks", []):
            entry = {"block_id": b.get("blockId", "")}
            if "textBlock" in b:
                tb = b["textBlock"]
                entry.update({"type": "text", "text": tb.get("text", ""), "semantic_type": tb.get("type", "paragraph")})
            elif "tableBlock" in b:
                tb = b["tableBlock"]
                rows = []
                for hr in tb.get("headerRows", []):
                    rows.append([" ".join(b2.get("textBlock", {}).get("text", "") for b2 in c.get("blocks", [])).strip() for c in hr.get("cells", [])])
                for br in tb.get("bodyRows", []):
                    rows.append([" ".join(b2.get("textBlock", {}).get("text", "") for b2 in c.get("blocks", [])).strip() for c in br.get("cells", [])])
                entry.update({"type": "table", "table_rows": rows, "table_row_count": len(rows)})
            all_blocks.append(entry)

    seen = set()
    md_parts = []
    for c in all_chunks:
        content = c["content"].strip()
        if content and content not in seen:
            seen.add(content)
            md_parts.append(content)
    md = "\n\n---\n\n".join(md_parts)
    md = md.replace("\ufb01", "fi").replace("\ufb02", "fl").replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    return md, all_chunks, all_blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 docai_batch_gcs.py <input.pdf> [output_dir]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_file)
    os.makedirs(output_dir, exist_ok=True)
    base = re.sub(r'[^a-zA-Z0-9._-]', '_', os.path.basename(input_file).rsplit(".", 1)[0])

    print(f"Processing (BATCH/GCS): {input_file}")
    t0 = time.time()
    creds = get_creds()

    gcs_input_path = f"input/{base}.pdf"
    gcs_output_prefix = f"output/{base}/"

    print("\n[1/4] Uploading to GCS...")
    gcs_upload(input_file, gcs_input_path, creds)

    print("\n[2/4] Starting batch process...")
    op_name = start_batch_process(
        f"gs://{GCS_BUCKET}/{gcs_input_path}",
        f"gs://{GCS_BUCKET}/{gcs_output_prefix}", creds)

    print("\n[3/4] Waiting for completion...")
    poll_operation(op_name, creds)

    print("\n[4/4] Downloading results...")
    output_files = gcs_list(gcs_output_prefix, creds)
    print(f"  Found {len(output_files)} output files")
    md, all_chunks, all_blocks = extract_from_batch(output_files, creds)

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
            "gcs_bucket": GCS_BUCKET, "operation": op_name,
            "elapsed_seconds": round(elapsed, 1), "markdown_length": len(md),
            "chunks": all_chunks, "layout_blocks": all_blocks,
            "metrics": {"total_chunks": len(all_chunks), "total_blocks": len(all_blocks),
                        "text_blocks": text_blocks, "table_blocks": table_blocks}
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved: {md_path}, {json_path}")

    # Cleanup GCS
    print("\nCleaning up GCS...")
    gcs_delete(gcs_input_path, creds)
    for obj in output_files:
        gcs_delete(obj["name"], creds)
    print("  Done.")


if __name__ == "__main__":
    main()
