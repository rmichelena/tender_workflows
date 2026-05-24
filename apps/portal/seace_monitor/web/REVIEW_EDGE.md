# Multi-Review E: storage / settings / SEACE proxy / UI edge cases

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `af977b5298df05a881a1d95adc5de073f9d656f2`  
**Scope:**
- `downloader.py`
- `document_storage.py`
- `process_storage.py`
- `entity_catalog.py`
- `entity_process_cleanup.py`
- `web/settings_entities.py`
- `web/seace_proxy.py`
- `web/static/*.js`
- templates + adjacent DB/client/parser/app context

**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Qwen 3.6 Plus, GLM-5.1  
**Kimi K2.6:** did not return usable findings before consolidation  
**Excluded by request:** security / hardening review

---

## Overall Assessment

This surface is mostly functional, but has several important edge-case reliability issues:

- settings save can partially commit before validation,
- bulk entity cleanup can delete files while jobs are active or before DB commit succeeds,
- SEACE proxy session handling is fragile under AJAX, multiple workers, and parallel browser requests,
- document/download paths need stronger retry/encoding/collision handling.

No security/hardening findings are included.

---

## Findings

### H1. Entity settings save can commit changes before rejecting invalid scan options
**Severity:** High  
**File:** `apps/portal/seace_monitor/web/settings_entities.py`  
**Flagged by:** GPT-5.5

`api_save_entities()` mutates entity `activa` flags, applies removed-entity cleanup, and commits before validating `added_scan_mode` / `since_date`.

**Impact:** endpoint can return HTTP 400 while DB/filesystem changes have already been committed.

**Fix:** Validate all request options first. Build `scan_options` before mutating entities or applying removal policies. Then perform DB/file mutations and commit once.

---

### H2. Bulk entity removal can delete files while download/analysis jobs are active
**Severity:** High  
**File:** `apps/portal/seace_monitor/entity_process_cleanup.py`  
**Flagged by:** GPT-5.5

`_DOWNLOADED_STATUSES` includes `descargando`, and bulk cleanup does not check `analysis.status == "running"`. Individual discard/archive routes guard these active states, but settings cleanup bypasses those protections.

**Impact:** removing an entity can delete `data_dir` or analysis records while a background download/analysis is using them.

**Fix:** Exclude `descargando` from destructive bulk cleanup. Check `proc.analysis.status == "running"` and defer cleanup unless stale. Return deferred counts in the settings response.

---

### H3. `/publicaciones/{id}/descartar` can mark non-publicada processes discarded without cleanup
**Severity:** High  
**File:** `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** GLM

The publicaciones discard endpoint only blocks `descargando` and already-`descartada`. If called directly for a `descargada`, `analizada`, or `portafolio` process, it sets status to `descartada` without cleaning `data_dir`, `documentos_json`, or analysis.

**Impact:** orphaned files and stale DB rows for discarded processes.

**Fix:** Only allow this endpoint for `publicada`, or route non-publicada statuses through the appropriate cleanup/archive handler.

---

### H4. `download_file()` lacks retry and can leave nonzero `.part` files after mid-stream failure
**Severity:** High  
**File:** `apps/portal/seace_monitor/downloader.py`  
**Flagged by:** Qwen

Downloads stream to `.part`, but if the connection drops after some bytes are written, the `.part` file can remain. The function only unlinks when `written == 0`.

**Impact:** partial files accumulate and retry behavior depends on external cleanup.

**Fix:** Wrap the streaming loop in `try/except` and unlink `.part` on any exception. Add 2–3 retries with backoff for transient SEACE/Alfresco failures.

---

### M1. Disk cleanup happens before DB commit, causing irreversible DB/filesystem inconsistency on rollback
**Severity:** Medium  
**Files:** `apps/portal/seace_monitor/process_storage.py`, `apps/portal/seace_monitor/web/settings_entities.py`  
**Flagged by:** GLM, DeepSeek

`discard_process_downloads()` deletes files from disk before DB metadata/analysis cleanup and before the caller commits. `apply_removed_entity_policy()` can also delete files before final commit.

**Impact:** if DB flush/commit fails, the DB rollback can restore references to files that have already been deleted.

**Fix:** Use a two-phase cleanup: mark DB rows for cleanup and commit first, then move files to a recoverable trash location, then final-delete. At minimum, perform DB operations and `flush()` before deleting files.

---

### M2. `discard_all` can re-archive already archived processes
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/entity_process_cleanup.py`  
**Flagged by:** DeepSeek

`_ANALYZED_STATUSES` includes `archivada`, and `discard_all` calls `archive_analyzed_process()` for those statuses. Already-archived data dirs can be moved/renamed inside trash again.

**Impact:** trash directories become confusing or duplicated; disk cleanup becomes harder.

**Fix:** Exclude `ProcessStatus.archivada` from archive operations, or make `archive_analyzed_process()` idempotent when `data_dir` is already under trash.

---

### M3. Archive/restore collision handling can delete or overwrite previous archives
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/process_storage.py`  
**Flagged by:** Qwen

`archive_analyzed_process()` disambiguates one collision but deletes the second collision with `shutil.rmtree(dest)`. `restore_archived_process()` also does not check a second destination collision.

**Impact:** repeated archive/restore cycles can destroy previous archive data.

**Fix:** Never delete an existing destination silently. Use unique timestamp/UUID suffixes, or verify the existing directory belongs to the same process and intended generation.

---

### M4. SEACE proxy misses JSF AJAX headers
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** GPT-5.5, GLM

`_forward_headers()` only forwards `Accept`, `Accept-Language`, and `Content-Type`. JSF/PrimeFaces AJAX requests often require `Faces-Request: partial/ajax` and `X-Requested-With`.

**Impact:** upstream SEACE may return full HTML instead of partial XML updates, breaking proxied UI controls.

**Fix:** Forward a small allowlist of JSF reliability headers: `Faces-Request`, `X-Requested-With`, and any observed PrimeFaces-specific headers required by SEACE.

---

### M5. SEACE proxy sessions are process-local and break with multiple uvicorn workers
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** DeepSeek

Proxy sessions are stored in a module-level `_sessions` dict. With `uvicorn --workers N`, each worker has its own dict.

**Impact:** browser cookie `seace_sid` can route to a different worker that has no corresponding SEACE session, causing intermittent broken pages.

**Fix:** Document/enforce single worker for proxy mode, use sticky sessions, or move session storage to a shared backend.

---

### M6. Shared `requests.Session` is not synchronized per proxy session
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** DeepSeek

The dict access is locked, but the returned `requests.Session` is used concurrently by parallel browser resource requests for the same `sid`.

**Impact:** cookie jar updates can interleave; SEACE session can become inconsistent.

**Fix:** Add a per-`sid` lock around upstream requests, or use per-request sessions with serialized cookies in a shared store.

---

### M7. SEACE proxy does not rewrite unquoted CSS `url(...)` paths
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** GLM

`_rewrite_text()` rewrites paths preceded by quotes, but CSS can use unquoted paths like `url(/seacebus-uiwd-pub/resources/icon.png)`.

**Impact:** proxied styles/images/fonts can 404.

**Fix:** Add a rewrite for `url(/seacebus-uiwd-pub/...)` patterns before the quote-based rewrite.

---

### M8. Auto-open SEACE failures are silent when `link_id` is stale/invalid
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** Qwen

If SEACE DOM changes or `link_id` cannot be resolved, auto-open may fail and the user just sees the buscador/list page.

**Impact:** UI appears to work but shows the wrong SEACE page.

**Fix:** Validate `link_id` shape before injecting/POSTing. When auto-open fails, show a visible fallback message/link or retry row resolution.

---

### M9. Document links are not URL-encoded
**Severity:** Medium  
**Files:** `apps/portal/seace_monitor/web/templates/descargado_detalle.html`, `apps/portal/seace_monitor/web/templates/analizado_detalle.html`  
**Flagged by:** GPT-5.5

Templates interpolate document relative paths directly into URL path segments. Filenames with `#`, `%`, `?`, or similar URL-significant chars can be truncated or decoded incorrectly.

**Impact:** document preview/download links can 404 for valid files.

**Fix:** Generate document URLs server-side using URL-quoted path segments, or add a Jinja filter using `urllib.parse.quote(..., safe="/")` and apply it consistently.

---

### M10. Descartar button is shown on downloaded-detail page for `analizada` processes but endpoint rejects it
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/templates/descargado_detalle.html`  
**Flagged by:** GLM

The detail route allows both `descargada` and `analizada`, but the discard endpoint only handles `descargada`.

**Impact:** user sees a button that fails with HTTP 400.

**Fix:** Hide the discard button for `analizada` and show archive/navigation to the analyzed route, or redirect analyzed processes to `/analizados/{id}`.

---

### L1. Proxy sessions are only evicted on new proxy requests
**Severity:** Low  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** GLM

Expired sessions remain in memory until another proxy request calls eviction.

**Impact:** up to `_SESSION_MAX` session objects linger during idle periods.

**Fix:** Add periodic cleanup in app lifespan or lightweight middleware.

---

### L2. Entity→Process FK has no explicit delete behavior
**Severity:** Low  
**File:** `apps/portal/seace_monitor/db/models.py`  
**Flagged by:** GLM

Current code deactivates entities rather than deleting them, but DB schema has no explicit `ondelete` policy.

**Impact:** future entity deletion changes can create FK errors/orphans.

**Fix:** Decide policy (`CASCADE` or `SET NULL`) and encode it in the FK/relationship before adding real delete flows.

---

### L3. Settings date validation message can be clearer
**Severity:** Low  
**File:** `apps/portal/seace_monitor/web/settings_entities.py`  
**Flagged by:** Qwen

A date like `31/02/25` passes client regex but fails backend parsing with a generic message.

**Fix:** Include the invalid value and clarify that the date must exist, not only match `dd/mm/yy`.

---

## Recommended Fix Order

1. **H1/H2/H3/M1** — make settings and cleanup operations transactional/deferred and job-aware.
2. **H4** — harden download retry and `.part` cleanup.
3. **M4/M5/M6/M7/M8** — make SEACE proxy reliable for JSF/AJAX/multi-worker/session concurrency.
4. **M2/M3** — make archive/discard idempotent and collision-safe.
5. **M9/M10** — fix user-visible document links and buttons.
6. **L1-L3** — cleanup polish.

## Notes

No security/hardening review was performed. Findings focus on correctness, reliability, UI behavior, data/file lifecycle, operational recoverability, and edge cases.
