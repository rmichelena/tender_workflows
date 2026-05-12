# Code Review v2: `scripts/` folder (post-fixes)

**Date:** 2026-05-12  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Kimi K2.6, GLM-5.1, Qwen 3.6 Plus  
**Scope:** `scripts/extractors/` — re-review after applying fixes from v1 review  

---

## ✅ Fixed from v1 (9/13 issues resolved)

1. ✅ **Filename sanitization unified** — `batch_runner` now uses same regex as extractors
2. ✅ **GCS pagination added** — `nextPageToken` loop implemented in `gcs_list`
3. ✅ **GCS cleanup in `finally` block** — orphaned files no longer leak on failure
4. ✅ **Hardcoded credentials removed** — moved to `extractors.conf` with `get_docai_config()`
5. ✅ **`sys.path.insert` removed** — no more machine-specific paths
6. ✅ **`get_creds()` has error handling** — try/except with actionable messages
7. ✅ **DRY violations addressed** — shared `common.py` (181 lines) centralizes `get_creds`, `fix_ligatures`, `sanitize_filename`, `extract_table_rows`, `build_markdown`, `load_config`
8. ✅ **Batch runner writes incrementally** — `append_result()` writes per-PDF instead of at end
9. ✅ **Token refresh in batch polling** — `creds_getter(force_refresh=True)` on 401

---

## 🔶 Remaining Issues (6 Medium, 2 Low)

### M1. `get_creds` checks `creds.expired` but not `creds.valid`
**File:** `common.py:64-66`  
**Flagged by:** Kimi, GLM

A token can be invalid without being expired (revoked, scope mismatch, malformed). The current code only refreshes on `expired`, potentially returning invalid credentials that fail on first use.

**Fix:** Change to `if not creds.valid:` instead of `if creds.expired:`.

### M2. Token refresh missing in online chunked mode
**File:** `docai_online.py:87-96`  
**Flagged by:** GLM

The batch extractor properly handles token refresh via `creds_getter`, but `docai_online.py` passes static `creds` directly to `process_single_chunk`. For large documents split into many chunks, the token may expire mid-processing.

**Fix:** Use the same `creds_getter` callable pattern in `docai_online.py`. Before each chunk, get fresh creds; on 401, force-refresh and retry.

### M3. Race condition in incremental `batch_summary.json` writes
**File:** `batch_runner.py:40-48`  
**Flagged by:** Kimi

`append_result` reads the full JSON, modifies in memory, and rewrites. If interrupted between `json.load` and `json.dump`, the file is corrupted. Concurrent runs would also corrupt each other.

**Fix:** Use atomic writes: write to a temp file and `os.replace()`, or switch to JSONL append mode.

### M4. `split_pdf` temp file leak on exceptions
**File:** `docai_online.py:57-76`  
**Flagged by:** Kimi, GLM

If `fitz.open()` or `new_doc.save()` throws (corrupt PDF, permissions), temp files from prior iterations are never cleaned up. No `try/finally` around temp file creation.

**Fix:** Wrap in `try/finally` ensuring `os.unlink(tmp_path)` for each created temp file. Or use `tempfile.TemporaryDirectory()`.

### M5. `creds_getter` has no retry on refresh failure in `poll_operation`
**File:** `docai_batch_gcs.py:153-165`  
**Flagged by:** Kimi

If `creds_getter(force_refresh=True)` throws (network down, token revoked), the poll loop breaks immediately with no retry. For batch operations lasting hours, transient network failures should be retried.

**Fix:** Wrap refresh in retry with backoff (e.g., 3 attempts with increasing sleep).

### M6. GCS cleanup incomplete if `gcs_list` failed
**File:** `docai_batch_gcs.py:257-260`  
**Flagged by:** Kimi

If `gcs_list` throws before cleanup, `output_files` is `[]` and only the input file gets deleted. Output objects that were actually created remain orphaned.

**Fix:** In the `finally` block, retry `gcs_list` before cleanup, or at minimum log a warning if `output_files` is empty but the batch operation reported success.

### L1. `sanitize_filename` strips Unicode without normalization
**File:** `common.py:91-93`  
**Flagged by:** Kimi

`"Café_Número_1.pdf"` becomes `"Caf__N_mero_1"` instead of `"Cafe_Numero_1"`. More readable output with NFKD normalization first.

**Fix:** Add `unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')` before the regex.

### L2. `import fitz` inside functions instead of module top
**File:** `docai_online.py:49, 57`  
**Flagged by:** Kimi

Code smell — makes dependency detection harder. Python caches modules so it works, but convention is top-level imports.

**Fix:** Move `import fitz` to the top of the file.

---

## Priority Order for Developer

1. **M1** (`creds.valid`) — one-line fix, prevents subtle auth failures
2. **M2** (online chunked refresh) — same class of bug that was fixed for batch, unfixed for online
3. **M3** (atomic writes) — prevents data loss in batch runner
4. **M4** (temp file cleanup) — resource leak on edge cases
5. **M5** (refresh retry) — resilience for long-running batch operations
6. **M6** (cleanup completeness) — storage cost prevention
7. **L1, L2** — quality improvements, non-blocking
