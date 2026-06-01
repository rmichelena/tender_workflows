# Multi-Review: ADP Portal Input Source

**Branch:** `adp-input` | **Commit:** `611b975` | **Scope:** 17 files (+1619/-15 lines)  
**Reviewed:** 2026-05-29  
**Reviewers:** GPT-5.5, GLM-5.1 (Z.Ai), DeepSeek V4 Pro (Fireworks)  
**Timed out:** Qwen 3.6 Plus (no useful output)

---

## Summary

The ADP Portal integration is well-structured, closely following established SEACE patterns with clean separation of parser/client/scanner/watchlist/downloader. No Critical issues. One High issue (auto-reject rules broken for ADP) and several Medium issues around reliability and data consistency. No regressions to existing SEACE functionality.

**Verdict:** ✅ Approve with fixes for 1 High + 4 Medium issues.

---

## High

### H1. Auto-reject rules silently fail for ALL ADP processes
**File:** `adp_scanner.py:181` | **Reviewers:** DeepSeek V4 Pro

`proc.objeto` is set to plural forms ("Bienes", "Servicios", "Consultorias", "Obras") but all 11 default auto-reject rules use singular forms with word-boundary matching (`\bservicio\b`). The trailing 's' in "servicios" invalidates the `\b` assertion, so **zero rules ever match any ADP process**. ADP procurement that should be discarded flows through to the main pipeline.

**Fix:** Map to singular forms matching the rule vocabulary:
```python
_OBJETO_MAP = {1: "Consultoría", 2: "Obra", 3: "Bien", 4: "Servicio"}
proc.objeto = _OBJETO_MAP.get(adp_proc.work_id, "Bien")
```
Or update rules to accept both forms: `objeto:(servicio OR servicios)`.

**Trace:** `apply_auto_reject_rules()` → `_contains_term("servicios", "servicio")` → regex `(?<![a-z0-9])servicio(?![a-z0-9])` → position 8 has 's' (word char) → `(?![a-z0-9])` fails → no match → all 11 rules fail → no auto-reject.

---

## Medium

### M1. Watchlist `_merge_archivo_from_storage` results discarded when `data_dir` is None
**File:** `adp_watchlist.py:165-171` | **Reviewers:** GPT-5.5, GLM-5.1, DeepSeek V4 Pro (unanimous)

After `_merge_archivo_from_storage` mutates `new_docs` to preserve `archivo` paths, `new_docs_json` is only re-serialized inside the `if proc.data_dir:` block. When `data_dir` is missing, `proc.documentos_json` is set to the original JSON with all `archivo=""`. The merge results are silently discarded.

**Fix:** Always re-serialize after merge:
```python
new_docs = json.loads(new_docs_json)
_merge_archivo_from_storage(new_docs, proc.documentos_json)
new_docs_json = json.dumps(new_docs, ensure_ascii=False)  # ← always

if proc.data_dir:
    docs_dir = Path(proc.data_dir) / "documentos"
    if docs_dir.is_dir():
        download_adp_documents(docs_dir, new_docs, client)
        new_docs_json = json.dumps(new_docs, ensure_ascii=False)
```

### M2. `AdpClient` session leaked on early return in `refresh_adp_watchlist`
**File:** `adp_watchlist.py:91` | **Reviewers:** GLM-5.1

If no ADP processes are in the watchlist, `return 0` exits without calling `client.close()`. No `try/finally` wrapper, so exceptions also leak the session.

**Fix:**
```python
client = AdpClient(http_proxy=config.http_proxy)
try:
    # ... existing body ...
finally:
    client.close()
```

### M3. `fetch_category_html` has no retry — transient errors skip entire category for 6h
**File:** `adp_client.py:64` | **Reviewers:** GPT-5.5

`download_document` has 3-retry loop with backoff, but `fetch_category_html` raises immediately on any network error. A transient DNS blip or 502 means that category gets no updates until the next 6h cycle.

**Fix:** Add retry wrapper (same pattern as `download_document`).

### M4. RuntimeError from empty download not retried
**File:** `adp_client.py:118` | **Reviewers:** GLM-5.1

`_download_once` raises `RuntimeError` for 0-byte responses, but `download_document` only catches `(requests.RequestException, OSError)`. Empty responses could be transient (server timeout generating PDF).

**Fix:** Add `RuntimeError` to the except tuple:
```python
except (requests.RequestException, OSError, RuntimeError) as exc:
```

### M5. Filename collision causes wrong file attribution
**File:** `adp_downloader.py:50` | **Reviewers:** GLM-5.1

When two documents produce the same sanitized filename (e.g., both titled "BASES DEL PROCESO DE SELECCIÓN"), the second skips download and points `archivo` to the first's file — wrong data. SEACE avoids this via `allocate_unique_path()`.

**Fix:** Use `allocate_unique_path` from `document_storage`:
```python
from .document_storage import allocate_unique_path
dest = allocate_unique_path(docs_dir, filename)
```

---

## Low

### L1. `content_hash` excludes `download_url` — silent stale documents if server changes file
**File:** `adp_parser.py:131` | **Reviewers:** GPT-5.5

If the portal updates a PDF keeping the same `name_file`/title/dates, the hash won't detect the change. Low because `name_file` appears to be content-derived in practice.

### L2. Watchlist fetches all 4 categories (~4MB) for single-process check
**File:** `adp_watchlist.py:93` | **Reviewers:** GPT-5.5, GLM-5.1

`refresh_adp_watchlist` always fetches all 4 categories regardless of how many processes need rechecking. Consider caching work_id mapping or storing it on Process.

### L3. `source_ref` identity fragile for codes with embedded descriptive text
**File:** `adp_parser.py:145` | **Reviewers:** DeepSeek V4 Pro

Codes like `"LPN-001-2026-AdP SEGUNDA CONVOCATORIA"` serve as identity keys. If the portal rephrases the suffix, a duplicate Process is created.

### L4. `_extract_anio` fallback to `datetime.now().year` can corrupt on re-scans
**File:** `adp_scanner.py:193` | **Reviewers:** DeepSeek V4 Pro

Codes without year patterns get `anio=datetime.now().year`. On re-scan in a different year with a content_hash change, the year silently updates. Fix: only set `anio` for new processes, or use config year as fallback.

---

## Positive Observations

- Clean separation: parser/client/scanner/watchlist/downloader each have single responsibility
- Immutable dataclasses with `frozen=True` for hash stability
- Robust fingerprinting with document order normalization
- Retry with exponential backoff in download path
- Safe `.part` + `replace()` atomic file writes
- Proper proxy support matching existing infrastructure
- Worker integration is clean — separate poll intervals, correct `min()` wake-up
- Savepoint isolation — single process failure doesn't roll back entire scan
- Source-aware UI routing preserves all SEACE functionality (no regressions)
- Good test coverage for parser (20 tests) and scanner (11 tests)
- Sanitized filenames via `sanitize_download_filename()`

---

## Recommended Fix Order

1. **H1** — Fix `objeto` to singular forms (1 line change, immediate impact)
2. **M1** — Move `new_docs_json` re-serialization outside `if data_dir` block
3. **M2** — Wrap `refresh_adp_watchlist` in `try/finally` for client lifecycle
4. **M5** — Use `allocate_unique_path` for collision-safe filenames
5. **M3** — Add retry to `fetch_category_html`
6. **M4** — Add `RuntimeError` to download retry tuple
7. Low items as time permits
