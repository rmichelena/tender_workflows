# Code Review v3: `scripts/` folder (final)

**Date:** 2026-05-12  
**Reviewers:** GPT-5.5, Kimi K2.6, GLM-5.1 (3rd pass)  
**Scope:** `scripts/extractors/` — final review after v2 fixes  
**Verdict:** ✅ Production-ready. Quality: 8/10.

---

## ✅ All v1+v2 issues resolved (21/21)

- ✅ Filename sanitization unified
- ✅ GCS pagination with nextPageToken
- ✅ GCS cleanup in finally block
- ✅ Credentials moved to extractors.conf
- ✅ sys.path.insert removed
- ✅ get_creds() error handling
- ✅ DRY — shared common.py
- ✅ Batch runner incremental writes
- ✅ Token refresh in batch polling
- ✅ creds.valid check in get_creds
- ✅ Online chunked mode creds_getter with refresh
- ✅ Batch runner timeout = extractor max_wait + 300
- ✅ Atomic writes via os.replace
- ✅ split_pdf cleanup in caller's finally
- ✅ gcs_cleanup enumerates by prefix as fallback
- ✅ _resolve_token_path uses actual conf_path
- ✅ sanitize_filename with NFKD normalization

---

## 🔶 Remaining Polish Items (2 Medium, 3 Low)

These are edge-case improvements, not blocking issues.

### M1. `split_pdf` can leak temp files on mid-split exception
**File:** `docai_online.py:53-70`  
**Flagged by:** GPT-5.5, Kimi, GLM (unanimous)

If `new_doc.save()` raises on iteration N, temp files from iterations 0..N-1 are already on disk but never added to `chunks`. The function raises before returning, so the caller's `finally` never runs.

**Fix:** Wrap the loop in try/except inside `split_pdf`, cleaning up already-created temp files before re-raising. Also add `doc.close()` in a `finally` block.

### M2. No validation that `gcs_bucket` is configured for batch mode
**File:** `docai_batch_gcs.py:42`  
**Flagged by:** Kimi, GLM

Empty `gcs_bucket` produces malformed GCS URLs with cryptic 404/400 errors instead of a clear config error.

**Fix:** Add `if not GCS_BUCKET: raise SystemExit("gcs_bucket is required for batch mode")` after config load.

### L1. `creds_getter` inconsistency: batch uses `.expired`, online uses `.valid`
**File:** `docai_batch_gcs.py` vs `docai_online.py`  
**Flagged by:** GPT-5.5, Kimi, GLM

Batch `creds_getter` checks `_creds[0].expired` while online checks `not _creds[0].valid`. The latter is broader and catches non-expiry invalidity.

**Fix:** Change batch to `not _creds[0].valid` for consistency.

### L2. Missing config keys produce unhelpful `NoOptionError`
**File:** `common.py:54`  
**Flagged by:** GLM

If `token_path`, `project_id`, or `processor_id` are missing from `[docai]` section, `configparser` raises a raw `NoOptionError` instead of a helpful message.

**Fix:** Validate required keys after loading, raise `SystemExit` with the list of missing keys.

### L3. `append_result` accumulates results from previous runs
**File:** `batch_runner.py:35-48`  
**Flagged by:** GPT-5.5

Re-running on the same output directory appends to existing `batch_summary.json`. Not a bug, but could surprise users expecting a fresh run.

**Fix:** Add a `--clean` flag or document the append behavior.

---

## Summary

After 3 review rounds across 5 models, the code is solid. All originally identified issues have been fixed. The 5 remaining items are polish — temp file cleanup edge case, config validation, and minor consistency issues. None are blocking for production use.

**Recommended next step:** Address M1 (split_pdf cleanup) and M2 (gcs_bucket validation) when convenient. The rest can be picked up iteratively.
