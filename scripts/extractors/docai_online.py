#!/usr/bin/env python3
"""
Google Document AI — Layout Parser (Online/Chunked mode).

Extrae texto + estructura de PDFs usando Google Document AI Layout Parser.
Sin dependencia de Google Cloud Storage — funciona con OAuth de usuario.

Modo ONLINE: documentos ≤ ONLINE_PAGE_LIMIT páginas (15 por defecto).
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
  - Credenciales OAuth en /opt/data/google_token_personal.json
  - Processor ID hardcodeado (layout parser del proyecto GCP)
"""

import sys, os, time, json, base64, re, tempfile

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
ONLINE_PAGE_LIMIT = 15

TOKEN_PATH = "/opt/data/google_token_personal.json"


def get_creds():
    """Obtiene y refresca credenciales OAuth."""
    with open(TOKEN_PATH) as f:
        creds = Credentials.from_authorized_user_info(json.load(f))
    if creds.expired:
        creds.refresh(Request())
    return creds


def count_pdf_pages(file_path):
    """Cuenta páginas de un PDF con PyMuPDF."""
    import fitz
    doc = fitz.open(file_path)
    n = len(doc)
    doc.close()
    return n


def split_pdf(file_path, chunk_size=ONLINE_PAGE_LIMIT):
    """Divide PDF en trozos de N páginas. Retorna lista de (temp_path, start, end)."""
    import fitz
    doc = fitz.open(file_path)
    total = len(doc)
    chunks = []
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_path = tmp.name
        tmp.close()
        new_doc = fitz.open()
        for pg in range(start, end):
            new_doc.insert_pdf(doc, from_page=pg, to_page=pg)
        new_doc.save(tmp_path)
        new_doc.close()
        chunks.append((tmp_path, start, end))
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
                "enableImageAnnotation": True,
                "chunkingConfig": {
                    "chunkSize": 500,
                    "includeAncestorHeadings": True
                }
            }
        }
    }
    url = f"{API_BASE}/v1/{PROCESSOR_NAME}:process"
    r = requests.post(url,
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=body, timeout=300)
    if r.status_code != 200:
        raise Exception(f"DocAI error {r.status_code}: {r.text[:500]}")
    return r.json()


def extract_from_response(result, page_offset=0):
    """Extrae chunks y blocks de una respuesta DocAI.
    
    NOTA: El texto completo está en chunkedDocument.chunks[].content,
    NO en document.text (que viene vacío para Layout Parser).
    """
    doc = result.get("document", {})

    chunks_raw = doc.get("chunkedDocument", {}).get("chunks", [])
    chunks = []
    for c in chunks_raw:
        page_span = c.get("pageSpan", {})
        if page_span:
            page_span = {
                "pageStart": page_span.get("pageStart", 1) + page_offset,
                "pageEnd": page_span.get("pageEnd", 1) + page_offset,
            }
        chunks.append({
            "chunk_id": c.get("chunkId"),
            "content": c.get("content", ""),
            "page_span": page_span,
        })

    blocks_raw = doc.get("documentLayout", {}).get("blocks", [])
    blocks = []
    for b in blocks_raw:
        entry = {"block_id": b.get("blockId", ""), "_page_offset": page_offset}
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
        blocks.append(entry)

    return [c["content"] for c in chunks], chunks, blocks


def build_markdown(all_chunk_texts):
    """Construye markdown final desde chunks, deduplicando."""
    seen = set()
    md_parts = []
    for ct in all_chunk_texts:
        normalized = ct.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            md_parts.append(normalized)
    md = "\n\n---\n\n".join(md_parts)
    md = md.replace("\ufb01", "fi").replace("\ufb02", "fl").replace("\ufb00", "ff")
    md = md.replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    return md


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 docai_online.py <input.pdf> [output_dir]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_file)
    os.makedirs(output_dir, exist_ok=True)
    base = re.sub(r'[^a-zA-Z0-9._-]', '_', os.path.basename(input_file).rsplit(".", 1)[0])

    print(f"Processing: {input_file}")
    t0 = time.time()
    creds = get_creds()
    pages = count_pdf_pages(input_file)
    print(f"Pages: {pages}")

    all_chunk_texts, all_chunks, all_blocks = [], [], []

    if pages <= ONLINE_PAGE_LIMIT:
        print(f"Mode: ONLINE")
        with open(input_file, "rb") as f:
            pdf_bytes = f.read()
        result = process_single_chunk(pdf_bytes, creds)
        ct, ch, bl = extract_from_response(result)
        all_chunk_texts.extend(ct); all_chunks.extend(ch); all_blocks.extend(bl)
    else:
        print(f"Mode: CHUNKED ({pages} pages in chunks of {ONLINE_PAGE_LIMIT})")
        pdf_chunks = split_pdf(input_file, ONLINE_PAGE_LIMIT)
        for i, (chunk_path, start_pg, end_pg) in enumerate(pdf_chunks):
            print(f"  Chunk {i+1}/{len(pdf_chunks)}: pages {start_pg+1}-{end_pg}...", end=" ", flush=True)
            with open(chunk_path, "rb") as f:
                chunk_bytes = f.read()
            try:
                result = process_single_chunk(chunk_bytes, creds)
                ct, ch, bl = extract_from_response(result, page_offset=start_pg)
                all_chunk_texts.extend(ct); all_chunks.extend(ch); all_blocks.extend(bl)
                print(f"OK ({len(ct)} chunks)")
            except Exception as e:
                print(f"ERROR: {e}")
            finally:
                os.unlink(chunk_path)

    md = build_markdown(all_chunk_texts)
    elapsed = time.time() - t0

    text_blocks = sum(1 for b in all_blocks if b.get("type") == "text")
    table_blocks = sum(1 for b in all_blocks if b.get("type") == "table")

    print(f"\nResults: {elapsed:.1f}s | {len(all_chunks)} chunks | {len(all_blocks)} blocks | {len(md):,} chars")

    # Save
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
                        "text_blocks": text_blocks, "table_blocks": table_blocks}
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved: {md_path}, {json_path}")


if __name__ == "__main__":
    main()
