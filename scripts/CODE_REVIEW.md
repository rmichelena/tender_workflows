# Code Review: `scripts/` folder

**Date:** 2026-05-12  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Kimi K2.6, GLM-5.1 (Qwen 3.6 timed out)  
**Scope:** `scripts/extractors/` — 4 Python files, ~720 lines  
**Files:** `batch_runner.py`, `docai_batch_gcs.py`, `docai_online.py`, `markitdown_extract.py`

---

## Summary

Consolidated findings after deduplication:

- **High: 5**
- **Medium: 6**
- **Low: 2**

---

## High

### 1. Filename sanitization mismatch causes silent data loss

`batch_runner.py` uses `str.isalnum()` (Unicode-aware, keeps accented chars like ó, ñ) while all extractors use `re.sub(r'[^a-zA-Z0-9._-]', '_')` (ASCII-only). For any PDF with non-ASCII characters in its name, the batch runner constructs a path that will never match the extractor output. The summary always reports 0 chars extracted even when extraction succeeded.

**Files:** `batch_runner.py:73` vs `docai_online.py:198`, `docai_batch_gcs.py:212`, `markitdown_extract.py:46`  
**Flagged by:** GPT-5.5, DeepSeek, GLM  
**Fix:** Replace `base_safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)` with `base_safe = re.sub(r'[^a-zA-Z0-9._-]', '_', base)` and add `import re`.

### 2. GCS list doesn't handle pagination — output truncated at 100 objects

`gcs_list` sets `maxResults: 100` with no `nextPageToken` loop. Large batch operations producing >100 output shards silently lose results beyond the first page.

**File:** `docai_batch_gcs.py:78`  
**Flagged by:** GPT-5.5, GLM  
**Fix:** Loop while `nextPageToken` is present in the response, accumulating items across pages.

### 3. GCS cleanup not in finally block — orphaned files on failure

If extraction, JSON parsing, or file writing fails after the batch job completes, cleanup is skipped. Input PDFs and output shards remain on GCS indefinitely, incurring costs and leaking document content.

**File:** `docai_batch_gcs.py:126-132`  
**Flagged by:** Kimi, GLM  
**Fix:** Wrap post-upload logic in `try/finally`, perform GCS cleanup in the `finally` block.

### 4. Hardcoded credentials and machine-specific paths

GCP PROJECT_ID, PROCESSOR_ID, and TOKEN_PATH (`/opt/data/google_token_personal.json`) are hardcoded in both DocAI files. Combined with `sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")` across all 4 files, these scripts only run on one specific machine and expose infrastructure details in source code.

**Files:** All 4 files  
**Flagged by:** All 4 reviewers  
**Fix:** Move credentials to environment variables (`DOCAI_PROJECT_ID`, `DOCAI_PROCESSOR_ID`, `GOOGLE_TOKEN_PATH`). Remove `sys.path.insert` and use a virtualenv or `requirements.txt`.

### 5. `get_creds()` has no error handling

FileNotFoundError, JSONDecodeError, and RefreshError all propagate as raw tracebacks with no context message. In batch pipelines processing dozens of documents, one bad credential state kills the entire run unhelpfully.

**Files:** `docai_online.py:65-70`, `docai_batch_gcs.py:73-78`  
**Flagged by:** DeepSeek, Kimi  
**Fix:** Wrap in try/except with actionable messages (`sys.exit(f"Token file not found: {TOKEN_PATH}")`, etc.).

---

## Medium

### 6. Deduplication by exact content drops legitimate repeated text

Both `docai_online.py` and `docai_batch_gcs.py` use a `seen` set to skip chunks with previously-seen content. Procurement documents commonly repeat legal clauses, form headers, or annex titles across pages — these get silently dropped, producing incomplete output.

**Files:** `docai_online.py:139`, `docai_batch_gcs.py:171`  
**Flagged by:** GPT-5.5, DeepSeek, Kimi, GLM  
**Fix:** Deduplicate by `(chunk_id, content)` instead of content alone, or remove deduplication entirely.

### 7. DRY violation — duplicated logic across 3+ files

`get_creds()`, `fix_ligatures()`, table-block extraction (~16 identical lines), filename sanitization, and config constants are copy-pasted across files. A bug fix in one copy must be manually replicated.

**Files:** All 4  
**Flagged by:** All 4 reviewers  
**Fix:** Extract shared code into a `common.py` module: `get_creds()`, config constants, `extract_table_rows()`, `fix_ligatures()`.

### 8. Chunked mode swallows errors — no record of partial failure

Failed chunks are printed to stdout but not tracked. The output JSON appears successful with no indication of missing sections.

**File:** `docai_online.py:116-122`  
**Flagged by:** GPT-5.5, Kimi, GLM  
**Fix:** Track failed chunks in a list, include `failed_chunks` in the output JSON, exit with non-zero code.

### 9. GCS upload loads entire PDF into RAM

`gcs_upload` calls `f.read()` to slurp the full file before posting. Large procurement PDFs (100+ MB with images) can cause OOM.

**File:** `docai_batch_gcs.py:84-85`  
**Flagged by:** DeepSeek  
**Fix:** Pass the file object directly to `requests.post(data=f)` — requests will stream the upload.

### 10. Batch runner loses all results on interruption

Results are accumulated in a list and only written to `batch_summary.json` after all PDFs complete. A crash or SIGINT mid-batch loses all prior work.

**File:** `batch_runner.py:115-118`  
**Flagged by:** DeepSeek  
**Fix:** Write incrementally — append each result to the JSON file as it completes.

### 11. No isolation between concurrent runs — GCS path collision

GCS paths are deterministic (`input/{base}.pdf`). Two simultaneous runs processing files with the same basename will overwrite each other's GCS objects silently.

**File:** `docai_batch_gcs.py:96-98`  
**Flagged by:** GLM  
**Fix:** Include a UUID or timestamp in GCS paths: `input/{run_id}/{base}.pdf`.

---

## Low

### 12. Credentials not refreshed during long batch operations

`get_creds()` is called once; for batch jobs lasting >1 hour, the OAuth token may expire during polling, failing the operation.

**File:** `docai_batch_gcs.py:125`  
**Flagged by:** GPT-5.5  
**Fix:** Refresh credentials inside the poll loop when a 401 is detected.

### 13. OAuth refresh has no timeout

`creds.refresh(Request())` can hang indefinitely on network issues.

**Files:** `docai_online.py:59`, `docai_batch_gcs.py:66`  
**Flagged by:** Kimi  
**Fix:** Pass a `Request()` instance with an explicit timeout.

---

## Recommendations

The three highest-impact fixes:

1. **Unify filename sanitization** — eliminates the silent data-loss bug for non-ASCII filenames
2. **Add GCS pagination** — prevents truncated output for large documents
3. **Extract shared utilities** — single place to fix bugs instead of 3+ copies

Additionally, moving credentials to environment variables and removing hardcoded `sys.path` inserts would make the scripts portable beyond the original development machine.
