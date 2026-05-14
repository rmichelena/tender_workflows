---
name: landingai-ade
description: "LandingAI ADE document extraction: PDF to Markdown via dpt-2-mini/latest models. SDK pitfalls, credit costs, large document handling, and the working extraction script."
version: 1.0.0
tags: [landingai, ade, pdf, extraction, ocr, document, dpt-2]
---

# LandingAI ADE — Document Extraction

Extract structured Markdown from PDFs using LandingAI's Agentic Document Extraction (ADE) API. Uses `dpt-2-mini` (fast, cheap) or `dpt-2-latest` (best quality).

## When to Use

- Extracting text/markdown from PDFs (tender documents, contracts, specs)
- Comparing extraction quality against Google DocAI
- Documents with mixed scanned+vector content
- Need fast extraction (30s for 58 pages vs 40+ min for DocAI v1.6)

## Working Script

**Location:** `/opt/data/workspace/tender_procurement/scripts/extractors/landingai_extract.py`

**Usage:**
```bash
export PYTHONPATH="/opt/data/home/.local/lib/python3.13/site-packages:$PYTHONPATH"

# Mini (1.5 credits/page)
python3 landingai_extract.py document.pdf --model dpt-2-mini

# Latest (3 credits/page)
python3 landingai_extract.py document.pdf --model dpt-2-latest

# Custom output dir
python3 landingai_extract.py document.pdf --output-dir /path/to/output/

# Skip post-processing
python3 landingai_extract.py document.pdf --no-clean
```

**Post-processing pipeline** (in `common.py`):
1. `clean_ade_output()` — strips `<::...::>` annotation blocks, fixes NUL bytes where ° should be
2. `fix_ligatures()` — normalizes LaTeX artifacts (`^{\circ}` → `°`, `\times` → `×`, etc.)

**Output files:**
- `{stem}_ade_{model_tag}.md` — cleaned markdown
- `{stem}_ade_{model_tag}.json` — metadata + chunks with page grounding

## SDK Pitfalls (CRITICAL — learned by burning credits)

### 1. create() returns only job_id
```python
# WRONG (old SDK version):
job = client.parse_jobs.create(...)
while job.status != "complete":  # AttributeError: no 'status'

# CORRECT:
create_resp = client.parse_jobs.create(...)
job_id = create_resp.job_id  # Only field available
job = client.parse_jobs.get(job_id=job_id)  # This has status
while job.status not in ("completed", "complete", "failed"):
    ...
```

### 2. Large documents: data=None + output_url
For documents >~50 pages, the SDK returns `data=None` and provides an `output_url` (pre-signed S3) instead:
```python
job = client.parse_jobs.get(job_id=job_id)
if job.data is not None:
    # Small doc — data inline
    markdown = job.data.markdown
elif job.output_url:
    # Large doc — download from S3
    raw = urllib.request.urlopen(job.output_url).read()
    result = json.loads(raw)
    markdown = result["markdown"]
    chunks = result["chunks"]
```
The output URL expires in ~1 hour. Fetch it promptly.

### 3. Don't create duplicate jobs
```python
# CHECK existing jobs first:
jobs = client.parse_jobs.list()
for j in jobs.jobs:
    if j.status == "completed":
        # Recover result, don't create new job
```
Each job costs 1.5-3 credits/page. A 135-page PDF = 202-405 credits. Don't burn them re-creating.

### 4. Class name is LandingAIADE, not LandingAI
```python
from landingai_ade import LandingAIADE
client = LandingAIADE(apikey="...")  # NOT api_key
```

## API Key & Package

- **API key:** `/opt/data/workspace/tender_procurement/scripts/extractors/.env_landingai` → `VISION_AGENT_API_KEY=...`
- **Key format:** Use raw string, NOT base64-decoded (looks like base64 but isn't)
- **Package:** `landingai-ade` at `/opt/data/home/.local/lib/python3.13/site-packages/`
- **Import requires:** `PYTHONPATH="/opt/data/home/.local/lib/python3.13/site-packages:$PYTHONPATH"`

## Credit Costs

| Model | Cost | Quality | Speed |
|---|---|---|---|
| `dpt-2-mini` | 1.5 credits/page | Good | ~30s for 58p |
| `dpt-2-latest` | 3 credits/page | Best | ~32s for 58p |

## Benchmarks (bases_estandar_lp_bienes.pdf, 58p mixed)

| Extractor | Time | Markdown | H2 headings | Credits |
|---|---|---|---|---|
| ADE dpt-2-latest | 32s | 163K chars | 23 | 174 |
| ADE dpt-2-mini | 33s | 144K chars | 26 | 87 |
| DocAI v1.6 batch | 43 min | 122K chars | 99 | — |
| DocAI v1.0 batch | 42s | 125K chars | 83 | — |

## Known Issues

1. **NUL bytes for °** — ADE outputs `\x00` where degree/ordinal symbol should be. `clean_ade_output()` fixes this.
2. **`<::...::>` annotation blocks** — ADE wraps logos, signatures, decorations in these. `clean_ade_output()` strips them all.
3. **No DOCX support** — Only PDF, images (PNG/JPG), spreadsheets (XLSX/CSV).
4. **Sparse headings** — ADE produces fewer H2 headings than DocAI. Trade speed for structure.
5. **custom_prompts only supports figure** — Cannot customize prompts for other chunk types.


## Repo Scripts

| Script | Description |
|--------|-------------|
| `scripts/extractors/landingai_extract.py` | Working extraction script (SDK v2 API) |
| `scripts/extractors/common.py` | Shared post-processing: `clean_ade_output()`, `fix_ligatures()`, `fix_chunk_spacing()` |
| `scripts/extractors/batch_runner.py` | Batch runner for multiple PDFs |
| `scripts/extractors/extractors.conf.example` | Config template (API keys, accounts) |
