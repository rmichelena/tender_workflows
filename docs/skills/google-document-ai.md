---
name: google-document-ai
description: "Google Document AI Layout Parser: PDF extraction via REST API (online + batch). Covers processor versions, batch GCS workflow, chunk parsing, markdown assembly, and error handling. No client libraries — pure REST + google-auth."
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [document-ai, gcp, pdf, ocr, extraction, batch, layout-parser]
---

# Google Document AI — Layout Parser

Extract structured markdown from PDFs (scanned or native) using Google Document AI Layout Parser processor via REST API. No client libraries — we use `google-auth` for OAuth tokens and `requests` for 3 REST endpoints.

## When to Use

- User needs to extract text/structure from PDFs (tender documents, contracts, specs)
- Batch processing of large scanned documents
- Comparing extraction quality across processor versions
- Any task involving Document AI processor operations

## Architecture Decision: REST over Client Libraries

Client libraries (`google-cloud-documentai`) pull 200MB+ dependencies for 3 endpoints. We maintain granular control over:
- `processorVersions` in URL (client libs don't expose this easily)
- Retry logic, timeout tuning, error classification
- Request/response structure for debugging

Use `google-auth` (lightweight) for token management + raw `requests` calls.

## Processor Versions

| Version | Speed (batch) | Capabilities | Use When |
|---------|---------------|-------------|----------|
| `pretrained-layout-parser-v1.0-2024-06-03` (stable) | ~0.7s/page | OCR, chunking, basic layout | Speed matters; no semantic features needed |
| `pretrained-layout-parser-v1.5-2025-08-25` (Flash 2.5) | ~12.7s/page | OCR, chunking, headings | ❌ **Not recommended** — slower than v1.6 with lower quality |
| `pretrained-layout-parser-v1.6-2026-01-13` (rc) | ~9.3s/page (scanned) / **~44.5s/page (mixed)** | OCR, chunking, header/footer, image annotations, proper markdown | **Default choice** — best quality/speed tradeoff |
| `pretrained-layout-parser-v1.6-pro-2025-12-01` | ~80s/page (113 min / 85p scanned) | Pro variant of v1.6 | ❌ **Much slower** than v1.6 standard; not worth it |

**Key surprise:** v1.5 (Flash 2.5) is **slower** than v1.6 — 18 min vs 13 min for 85 pages. Counterintuitive; possibly queue priority or backend allocation. Don't assume "Flash" = faster.

Version is specified in the URL path:
```
.../processorVersions/{version_name}:process
.../processorVersions/{version_name}:batchProcess
```

## API Endpoints

### Online (single document, <5 pages)
```
POST https://{location}-documentai.googleapis.com/v1/projects/{project}/locations/{location}/processors/{processor}/processorVersions/{version}:process
```

### Batch (large documents, async via GCS)
```
POST https://{location}-documentai.googleapis.com/v1/projects/{project}/locations/{location}/processors/{processor}/processorVersions/{version}:batchProcess
```

### Poll operation
```
GET https://{location}-documentai.googleapis.com/v1/{operation_name}
```

## Critical API Details

### Batch Request Body (CORRECT field names)
```json
{
  "inputDocuments": {
    "gcsDocuments": {
      "documents": [{"gcsUri": "gs://bucket/input/file.pdf", "mimeType": "application/pdf"}]
    }
  },
  "documentOutputConfig": {
    "gcsOutputConfig": {"gcsUri": "gs://bucket/output/"}
  },
  "processOptions": {
    "layoutConfig": {
      "chunkingConfig": {"chunkSize": 500, "includeAncestorHeadings": true}
    }
  }
}
```

**PITFALL:** The field is `documentOutputConfig.gcsOutputConfig` — NOT `documentOutput.gcsOutputDestination`. The wrong names return HTTP 400 "Unknown name" error.

### Mandatory processOptions
- `layoutConfig.chunkingConfig.chunkSize: 500` — required for chunked output
- `includeAncestorHeadings: true` — chunks include parent headings for context

### Where the text lives
`document.text` comes **empty** in Layout Parser. Always use:
```
chunkedDocument.chunks[].content  →  the actual text/markdown per chunk
documentLayout.blocks[]           →  semantic structure (type: paragraph/table/header/footer)
```

### Image Annotations (v1.6 only)
`enableImageAnnotation` is boolean — no "short description" mode. When enabled:
- ~87% of chunks become image annotations (logos, stamps, signatures) — mostly noise
- Each annotation starts with `__START_OF_ANNOTATION__` marker
- **Recommendation:** Disable by default, use two-pass strategy if needed:
  1. Pass 1: `enableImageAnnotation=false` + filter headers/footers → clean markdown
  2. Pass 2 (optional): `enableImageAnnotation=true` + LLM classification of annotations

## Batch Workflow

1. **Upload** PDF to GCS `gs://{bucket}/input/{run_id}/`
2. **Start** batch process → get operation name
3. **Poll** operation every 20s until `done: true`
4. **Get output path** from `metadata.individualProcessStatuses[0].outputGcsDestination` — **DO NOT guess from run_id**
5. **Download** output JSONs using the metadata path as GCS listing prefix
6. **Parse** chunks + blocks, assemble markdown
7. **Cleanup** GCS (input + output objects)
8. **Upload** markdown to Drive

### Polling Pattern
```python
import time, requests
from google.auth.transport.requests import Request

def poll_operation(op_name, creds, api_base, poll_interval=20, max_wait=7200):
    url = f"{api_base}/v1/{op_name}"
    start = time.time()
    while True:
        if not creds.valid:
            creds.refresh(Request())  # NOTE: Request() with no args — google-auth limitation
        r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
        result = r.json()
        if result.get("done"):
            return result
        if time.time() - start > max_wait:
            raise TimeoutError(f"Operation {op_name} timed out")
        time.sleep(poll_interval)
```

## Error Handling

### HTTP Error Classification
| Status | Category | Action |
|--------|----------|--------|
| 400 | INVALID_ARGUMENT | Fix request, don't retry |
| 401/403 | AUTH/PERMISSION | Refresh creds; check IAM |
| 404 | NOT_FOUND | Wrong processor ID or version name |
| 429 | RATE_LIMIT | Retry with backoff |
| 500/502/503/504 | TRANSIENT | Retry with exponential backoff |

### Retry Pattern (exponential backoff + jitter)
```python
import random, time

def retry_request(fn, max_retries=2, base_delay=1.0):
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except DocAIError as e:
            if not e.is_transient or attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
```

## Configuration

`extractors.conf` (gitignored, next to scripts):
```ini
[docai]
project_id = 839375208239
location = us
processor_id = b8ea939312a8ff4
processor_version = pretrained-layout-parser-v1.6-2026-01-13
token_path = /opt/data/google_token_personal.json
gcs_bucket = hermoberto
chunk_size = 500
enable_image_annotation = false
filter_headers_footers = true
poll_interval = 20
max_wait = 7200
```

## Alternative: LandingAI ADE

LandingAI's Agentic Document Extraction is a competing SaaS with built-in structured extraction, document splitting, page classification, and no GCS/IAM setup required. SDK: `landingai-ade` v1.12.0 (env var: `VISION_AGENT_API_KEY`).

**Benchmarked (May 2026) on bases_estandar_lp_bienes.pdf (58p mixed):**

- **ADE dpt-2-latest:** 32s, 163K chars, 23 H2 — **80x faster** than DocAI v1.6, more text captured, but sparse headings
- **ADE dpt-2-mini:** 33s, 144K chars, 26 H2 — half the credits (87 vs 174), misses attestations/figure descriptions
- **DocAI v1.6:** 43 min, 122K chars, 99 H2 — **4x better heading structure**, proper markdown tables
- **DocAI v1.0:** 42s, 125K chars, 83 H2 — speed comparable to ADE but less text captured

**Strategy:** Use ADE for speed + text coverage + built-in `extract(schema=...)`. Use DocAI v1.6 when heading structure matters. See `references/landing-ai-ade-alternative.md` for full benchmark, SDK setup, API key pitfall (use key AS-IS, don't base64-decode), post-processing (`clean_ade_output`), and hybrid approach recommendation. **User verdict (May 2026): ADE dpt-2-latest is the chosen parser for the tender pipeline.**

### Extractor Selection Guide (user-confirmed, May 2026)

| Document type | Recommended | Reason |
|---|---|---|
| **PDF with scanned pages/fragments** | LandingAI ADE `dpt-2-latest` | Best speed/quality ratio, OCR works on mixed docs |
| **DOCX without critical graphics** | MarkItDown | No OCR needed, fast, free |
| **PDF purely vectorial** | MarkItDown | No OCR needed, fast, free |
| **PDF scanned pure, max quality** | DocAI v1.6 batch | Best heading structure, but slow |

**Rule of thumb:** If the document has *anything* scanned (signatures, stamps, scanned pages, diagrams), ADE is the best speed/quality tradeoff. If it's purely digital, MarkItDown suffices. For DOCX, always MarkItDown (ADE doesn't support DOCX).

## Working Extraction Scripts

All scripts are in `/opt/data/workspace/tender_procurement/scripts/extractors/`:

| Script | Extractor | Usage |
|---|---|---|
| `landingai_extract.py` | LandingAI ADE (dpt-2-mini/latest) | `python3 landingai_extract.py file.pdf --model dpt-2-mini` |
| `common.py` | Shared post-processing | `clean_ade_output()`, `fix_ligatures()`, `fix_chunk_spacing()` |

### LandingAI ADE — Extraction Script Details

**Script:** `/opt/data/workspace/tender_procurement/scripts/extractors/landingai_extract.py`

**SDK pitfalls (learned the hard way, May 2026):**

1. **`create()` returns only `job_id`** — `ParseJobCreateResponse` has NO `status` field. Must call `get(job_id=...)` separately to poll.
2. **Large documents return `data=None` with `output_url`** — For docs >~50 pages, the SDK doesn't populate `data` inline. Instead, `job.output_url` points to a pre-signed S3 URL with the full JSON result. The script now handles this: if `data is None`, downloads from `output_url`, parses JSON, extracts `markdown` and `chunks`.
3. **Job status strings** — Can be `"completed"` or `"complete"`. Check both. Also check `"failed"`.
4. **Polling interval** — Large PDFs (100+ pages) can take 5-10 minutes. Use 10s intervals, don't timeout too early.
5. **Credits** — `dpt-2-mini` = 1.5 credits/page, `dpt-2-latest` = 3 credits/page. A 135-page PDF costs 202 credits (mini) or 405 (latest).
6. **Don't re-create jobs** — Always `list()` existing jobs and recover results before creating new ones. Each failed attempt burns credits.

**API key location:** `scripts/extractors/.env_landingai` → `VISION_AGENT_API_KEY=...`

**Package:** `landingai-ade` installed at `/opt/data/home/.local/lib/python3.13/site-packages/`. Requires `PYTHONPATH=/opt/data/home/.local/lib/python3.13/site-packages` before import.

### Google DocAI — Batch Processing Quick Reference

**Authentication:** OAuth2 user token at `/opt/data/google_token_personal.json`. Requires scopes: `cloud-platform` + `devstorage.read_write` + `drive` (and others). If token expired, re-auth via PKCE flow:
```
python3 /opt/data/skills/productivity/google-workspace/scripts/google_api.py auth-url
# → open URL in browser → approve → exchange code
```

**Batch workflow script (no standalone script yet — run inline):**
```python
# 1. Upload to GCS
upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?uploadType=media&name={gcs_path}"
# POST with Authorization: Bearer {token}, Content-Type: application/pdf

# 2. batchProcess
POST https://{location}-documentai.googleapis.com/v1/projects/{project}/locations/{location}/processors/{processor}/processorVersions/{version}:batchProcess
Body: {inputDocuments, documentOutputConfig, processOptions}

# 3. Poll operation
GET https://{location}-documentai.googleapis.com/v1/{operation_name}
# Every 20s until done=true

# 4. Get output path from metadata.individualProcessStatuses[0].outputGcsDestination
# 5. List+download output JSONs from GCS using that path as prefix
# 6. Parse chunks, assemble markdown (use fix_chunk_spacing for v1.0, fix_ligatures for all)
# 7. Cleanup GCS objects
```

**Config:**
```ini
project_id = 839375208239
location = us
processor_id = b8ea939312a8ff4
gcs_bucket = hermoberto
token_path = /opt/data/google_token_personal.json
```

---

## Known Pitfalls

1. **`document.text` is empty** — Layout Parser only populates chunks. Read `chunkedDocument.chunks[].content`.
2. **Batch field names** — `documentOutputConfig`/`gcsOutputConfig`, NOT `documentOutput`/`gcsOutputDestination`.
3. **`Request(timeout=)` not supported** — `google-auth`'s `Request()` takes no timeout arg. Use `Request()` with no args.
4. **PYTHONUNBUFFERED=1** — Always set this for background Python processes, or output is buffered and invisible.
5. **v1.6 image annotations are noisy** — Disable by default; 7/8 chunks were image annotations in testing.
6. **v1.6 doesn't detect technical diagrams** — Only logos, stamps, signatures. Don't expect circuit/blueprint parsing.
7. **v1.0 has no header/footer detection** — v1.6 adds `semantic_type: header/footer` on layout blocks.
8. **Chunk deduplication needed** — DocAI sometimes produces overlapping chunks; dedup by page anchor + content hash.
9. **GCS cleanup** — Always delete input + output objects after download to avoid storage charges.
10. **OAuth token refresh** — Long-running polls may need `creds.refresh(Request())`. Check `creds.valid` before each request.
11. **DocAI service agent IAM** — Batch mode requires `service-{project}@gcp-sa-prod-dai-core.iam.gserviceaccount.com` with `roles/storage.objectAdmin` on the GCS bucket.
12. **v1.0 chunks have glued headings** — DocAI v1.0 concatenates headings with body text without line breaks: `1.1. REFERENCIASCuando` instead of `1.1. REFERENCIAS\n\nCuando`. Also runs sections together: `...de ser el caso.1.2. ALCANCELa`. **Always apply `fix_chunk_spacing()`** to v1.0 chunk content before assembling markdown. v1.6 does NOT have this problem — it produces proper `## 1.1. REFERENCIAS` headings natively. See `references/chunk-spacing-fix.md` for the regex patterns.
13. **v1.6 batch is faster than estimated** — Online mode: ~67s/page. Batch mode: ~9.3s/page (85 pages in 788s). Don't extrapolate from online benchmarks to batch.
14. **Run v1.0 and v1.6 in parallel** — For important docs, launch both batch jobs simultaneously with different `run_id`. v1.0 finishes in ~1 min (immediate usable output with spacing fix), v1.6 in ~10-15 min (superior quality). Use v1.0 while waiting, replace with v1.6 when ready.
15. **LaTeX `^{\circ}` instead of `°`** — DocAI OCR renders the degree/ordinal symbol (°) as LaTeX notation. v1.0: `N^{\circ} 32069`. v1.6: `\(N^{\circ}\) 32069` (wrapped in math delimiters). v1.5 is the only version that outputs `N°` natively. **Always normalize** in `fix_ligatures()` post-processing. Do NOT leave LaTeX in output markdown; it confuses downstream LLMs and is unreadable for humans. See `references/latex-normalization.md` for regex patterns.
16. **v1.5 is NOT the sweet spot** — Despite being "Flash 2.5", v1.5 took 18 min vs v1.6's 13 min for 85 pages. It also has poor list formatting (numbered sub-items concatenated into single paragraph) while v1.6 separates them with bullets. v1.6 remains the default choice for quality; v1.0 for speed.
17. **v1.5 native N°** — Only version that doesn't produce LaTeX for ° symbol. If you're using v1.5 specifically, the `fix_ligatures` LaTeX normalization is still safe (no-op) but unnecessary for N°.
18. **Batch output path is NOT `output/{run_id}/`** — DocAI adds `{operation_id}/{shard_index}/` subdirectories. Actual path: `output/{run_id}/{numeric_op_id}/0/`. Listing with prefix `output/{run_id}/` returns 0 files if you don't include the subdirs. **CRITICAL:** After operation completes, read `metadata.individualProcessStatuses[0].outputGcsDestination` to get the real output prefix. Example: `gs://hermoberto/output/66b43619/15433542583232725141/0`. Use this as your GCS listing prefix. Guessing the path from `run_id` alone causes silent data loss (poller downloads 0 files, then cleans up GCS).
19. **Batch `gcsOutputConfig` field is `gcsUri`** — NOT `gcsUriPrefix`. Using `gcsUriPrefix` returns HTTP 400: `Unknown name "gcsUriPrefix" at 'document_output_config.gcs_output_config': Cannot find field.` The field name `gcsUri` is correct for `batchProcess`.
20. **Heredoc inline Python in background processes produces 0 output** — Even with `PYTHONUNBUFFERED=1`, using `<< 'PYEOF'` heredoc syntax for inline Python in background terminal commands results in zero visible stdout. Use `python3 -c "..."` or write to a temp `.py` file instead. This caused 30+ min of silent polling with no way to see progress or debug.
21. **v1.6 is 3-5x slower on mixed (vectorial+scanned) documents** — 85 scanned-only pages: 13 min (9.3s/page). 58 mixed pages: 43 min (44.5s/page). Vectorial pages with complex tables/formatting require heavier Gemini inference. Don't extrapolate batch speed from scanned-only benchmarks. For mixed docs, always launch v1.0 in parallel as a speed-safe fallback.
22. **LaTeX normalization order matters** — Process `\times` and `^{N}` BEFORE unwrapping `\(...\)`. If you unwrap first, you need double-escaped regexes for content that was inside the wrapper, which are fragile. See `references/latex-normalization.md` for the correct processing order and all observed patterns.
23. **DocAI produces LaTeX for dimensions, footnotes, and °** — Not just `^{circ}`. Also: `\times` (× in measurements), `Word^{N}` (footnote superscripts), bare `^{N}`, and general `\(...\)` math wrappers around plain text. Always apply the full `fix_ligatures()` pipeline, not just the ° normalizer.
24. **LandingAI ADE API key must be used raw, NOT base64-decoded** — The key looks like base64 but decoding it to `id:secret` format causes HTTP 401. Use the original string as `VISION_AGENT_API_KEY` value directly.
25. **ADE NUL bytes where ° should be** — ADE dpt-2-latest outputs `\x00` in some (not all) positions where the degree/ordinal symbol (°) belongs (e.g., `LICITACION PUBLICA N\x001-2023`). These are invisible in editors but break markdown viewers. `clean_ade_output()` in common.py handles this. Always run it after ADE extraction.
26. **ADE `<::...::>` annotation blocks must be stripped** — ADE dpt-2-latest wraps logos, signatures, page decorations in `<::type: description::>` blocks (160 attestations + 8 logos + 43 figures in a 58p doc). These are noise for LLM consumption. No API option to disable. `clean_ade_output()` removes all of them. `dpt-2-mini` does NOT produce these.
27. **ADE does NOT support DOCX** — Only PDF, images (PNG/JPG), and spreadsheets (XLSX/CSV). For DOCX, convert to PDF first. SDK docstrings confirm: "The file can be a PDF or an image."
28. **ADE `custom_prompts` only supports `figure`** — The `CustomPrompts` TypedDict has one optional key: `figure: str`. Cannot customize prompts for attestation, logo, table, or other chunk types.
29. **v1.6-pro is 8x slower than v1.6 standard** — v1.6 standard: ~9.3s/page. v1.6-pro: ~80s/page (113 min for 85p scanned). Not worth the extra cost unless quality difference is proven.
30. **LandingAI SDK returns `data=None` with `output_url` for large documents** — For multi-page PDFs (135p observed), `ParseJobGetResponse.data` is `None` but `output_url` contains a pre-signed S3 URL to the full JSON result (`{markdown, chunks, splits, grounding, metadata}`). The URL expires in ~1 hour. Always check `output_url` when `data is None` before treating it as a failure. Download with `urllib.request.urlopen(job.output_url)` and parse the JSON manually.
31. **LandingAI: always list existing jobs before creating new ones** — `client.parse_jobs.list()` returns all recent jobs with status. Before submitting a new extraction, check if a completed job already exists for the same document. Creating duplicate jobs wastes credits (~1.5 credits/page for dpt-2-mini, ~3 credits/page for dpt-2-latest). Recover results from completed jobs with `client.parse_jobs.get(job_id=...)` + `output_url` download.
32. **LandingAI SDK `create()` returns only `job_id`** — `ParseJobCreateResponse` has only `job_id` field, no `status`. Polling requires `client.parse_jobs.get(job_id=create_resp.job_id)` which returns `ParseJobGetResponse` with `status`, `data`, `output_url`, `progress`, `failure_reason`. Status values observed: `pending`, `processing`, `completed`, `failed`.

## Files

- `references/version-comparison.md` — Detailed v1.0 vs v1.6 comparison, benchmark data, quality samples
- `references/api-quirks.md` — HTTP error transcripts, field name gotchas, reproduction recipes
- `references/chunk-spacing-fix.md` — Regex patterns for fixing DocAI's glued headings (v1.0), with test cases
- `references/latex-normalization.md` — Regex patterns for normalizing DocAI LaTeX artifacts (°, dimensions, footnotes)
- `references/landing-ai-ade-alternative.md` — LandingAI ADE competitor comparison, SDK, API, pros/cons vs DocAI
- `references/local-parser-apis.md` — Self-hosted parser comparison (Kreuzberg, Docling Serve, OpenDataLoader): endpoints, benchmarks, recommendations


## Repo Scripts

| Script | Description |
|--------|-------------|
| `scripts/extractors/docai_batch_gcs.py` | Batch mode via GCS (recommended for >15 pages). Uploads PDF to GCS, polls async operation, downloads results. |
| `scripts/extractors/docai_online.py` | Online/chunked mode. Sends pages in chunks, assembles result. Good for <15 pages or quick tests. |
| `scripts/extractors/common.py` | Shared utilities: auth (`get_creds`), config (`get_docai_config`), parsing (`parse_chunks`, `parse_layout_blocks`, `build_markdown`), error handling (`retry_request`, `classify_http_error`). |
| `scripts/extractors/extractors.conf.example` | Config template (project ID, processor, bucket, accounts) |
