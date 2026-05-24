# Multi-Review A: Gemini + documentos

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `d1baec23f1bcc7dafb70ae70b83188fcdb59e3ea`  
**Scope:**
- `apps/portal/seace_monitor/analysis/`
- `apps/portal/seace_monitor/web/analysis_chat.py`

**Adjacent context:** `document_storage.py`, `process_storage.py`, `tenant_paths.py`, `db/`  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Kimi K2.6, Qwen 3.6 Plus, GLM-5.1  
**Excluded by request:** security / hardening review

---

## Overall Assessment

The architecture is understandable and the module has the right pieces: document preparation, Gemini upload/retry helpers, free-reader analysis, session cache, chat, and the bridge to the deterministic stage-C scripts.

The main risks are lifecycle and idempotency issues:

- Gemini remote uploads/caches can be orphaned on crash, timeout, failed cache creation, or cleanup errors.
- `gemini_session.json` is a shared mutable file without locking/atomic writes.
- Concurrent analyze/chat requests can overwrite each other or delete each other's cache.
- Reruns can destroy the previous successful analysis before the replacement succeeds.
- Some Gemini calls and stage-C bridge paths lack the same retry/timeout/error-contract rigor as the main free-reader path.

No security/hardening findings are included.

---

## Findings

### C1. Concurrent analyze calls can corrupt Gemini cache and DB/session state
**Severity:** Critical  
**Files:** `apps/portal/seace_monitor/analysis/runner.py`, `apps/portal/seace_monitor/analysis/gemini_session.py`  
**Flagged by:** Qwen

Two concurrent `analyze(process_id)` calls can both reset the analysis row, run the long pipeline, and finalize Gemini session state. Each finalization can call `cleanup_gemini_session()` and `initialize_session_after_analysis()`, so one run may delete the other run's cache or overwrite `gemini_session.json`.

**Impact:** both jobs may report success, but the surviving session can point to a deleted/invalid cache, and DB fields can reflect whichever job finished last.

**Fix:** Add a per-process analysis lock (`{proc_dir}/.analysis.lock`, DB advisory lock, or job row with run id) covering the whole analysis pipeline. Also keep a run generation token and verify it before applying final DB/session changes.

---

### C2. Concurrent chat requests lose turns due to unlocked JSON read/modify/write
**Severity:** Critical  
**File:** `apps/portal/seace_monitor/analysis/gemini_session.py`  
**Flagged by:** GPT-5.5, DeepSeek, GLM, Kimi

`send_chat_message()` loads `gemini_session.json`, calls Gemini, appends user/model turns, and saves the whole JSON file. Concurrent POSTs for the same process both read the same history and last writer wins.

**Impact:** chat history silently loses messages; future chat context diverges from what the user saw.

**Fix:** Use a file lock around the entire `load → maybe rebuild → call Gemini → append → save` sequence, and save atomically via temp file + rename. Better long-term: store chat turns in SQLite with append-only sequence rows.

---

### C3. Gemini uploads can be orphaned when upload succeeds but activation/wait fails
**Severity:** Critical  
**File:** `apps/portal/seace_monitor/analysis/gemini_client.py`  
**Flagged by:** GPT-5.5, DeepSeek

`upload_file_with_retry()` uploads a file, then waits for Gemini processing. If `client.files.upload()` succeeds but `wait_file_active()` times out or raises before returning, the uploaded remote file handle is lost. The caller never receives it, so cleanup cannot delete it.

**Impact:** remote Gemini files accumulate invisibly, consuming quota/storage and increasing cleanup ambiguity.

**Fix:** Capture the uploaded file immediately. If `wait_file_active()` fails, best-effort delete that file before retrying or raising. Consider retrying polling the same uploaded file instead of re-uploading.

---

### H1. Failed cache/session cleanup can delete the only local reference to a remote cache
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/gemini_session.py`  
**Flagged by:** GLM

`cleanup_gemini_session()` catches/logs failures from `delete_remote_cache()` but still deletes the local `gemini_session.json` file.

**Impact:** if remote cache deletion fails, the `cache_name` is lost locally and the cache becomes unrecoverable except by TTL expiry.

**Fix:** Delete the session file only after cache deletion succeeds, or persist failed cache names to an orphan cleanup log/queue before removing the JSON.

---

### H2. `create_document_cache_from_uploads()` leaks uploaded files if cache creation fails
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/gemini_session.py`  
**Flagged by:** Qwen, Kimi

`create_document_cache_from_uploads()` receives uploaded files and creates a Gemini cache. If `client.caches.create()` fails, the uploaded files are not deleted. The similar `create_document_cache()` path has cleanup, but this path does not.

**Impact:** failed cache creation leaves remote Gemini files orphaned.

**Fix:** Wrap cache creation in `try/except` and call `delete_remote_files()` on failure unless ownership has explicitly been transferred.

---

### H3. Cache rebuild/archive breaks because `upload_paths` are absolute
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/gemini_session.py`  
**Flagged by:** GPT-5.5, DeepSeek, Kimi

`gemini_session.json` stores absolute `upload_paths`. When process directories move — e.g. archive to trash or tenant layout migration — those paths become stale. Chat is still allowed for archived processes, but cache rebuild fails after TTL because the files appear missing.

**Impact:** archived/migrated analyses cannot rebuild Gemini cache even though documents still exist in the moved process directory.

**Fix:** Store upload paths relative to `proc_dir`, or remap stored paths on load using current process directory. Rewrite session JSON during archive/restore/migration.

---

### H4. Failed reruns erase the previous successful analysis before replacement succeeds
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** GPT-5.5

`analyze()` resets the existing `AnalysisResult` and commits before running the long Gemini pipeline. If a rerun fails due to Gemini limits, conversion errors, or selected document issues, the previous successful `raw_json` / summary fields have already been cleared.

**Impact:** transient rerun failures destroy the last good analysis.

**Fix:** Stage rerun output separately (temp row, run table, or local variables). Replace the existing successful result atomically only after the new pipeline succeeds. On failure, preserve old result and record rerun error separately.

---

### H5. `run_axis0_gemini()` lacks retry/timeout handling
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** GLM, Qwen

`run_axis0_gemini()` calls `client.models.generate_content()` directly, unlike other Gemini calls that use retry helpers. A transient 429/500/503 can fail the entire stage-C bridge after deterministic work has already succeeded.

**Impact:** avoidable pipeline failures and repeated expensive reruns.

**Fix:** Use `generate_content_with_retry()` from `gemini_client.py`, with consistent timeout/backoff/error messaging.

---

### H6. `_apply_result()` truthiness fallback can preserve stale values
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** GLM

`_apply_result()` uses expressions like `stage1.get("alcance") or analysis.alcance`. Empty strings from a new analysis are treated as false and can preserve previous/stale values.

**Impact:** rerun results can contain stale fields instead of accurately reflecting the new output.

**Fix:** Use explicit `is not None` checks or assign fields unconditionally from the new result after staging.

---

### M1. Stale analysis recovery does not clean Gemini resources
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/db/maintenance.py`  
**Flagged by:** Qwen, DeepSeek

`recover_stale_analyses()` marks stale DB rows as `error` but does not clean related Gemini session/cache/uploads or local `fast_analysis/` artifacts.

**Impact:** crashes during analysis can leave local and remote artifacts behind until TTL/manual cleanup.

**Fix:** During stale recovery, call `cleanup_gemini_session()` where a valid `proc_dir` exists, and clean known temporary workspace files. Add orphan queue handling for failed remote deletes.

---

### M2. `wait_file_active()` treats FAILED or unknown Gemini file state as ready
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/gemini_client.py`  
**Flagged by:** DeepSeek

The polling helper returns when state is not `PROCESSING`. That means `FAILED` or missing/unknown state can be accepted as if active, producing downstream cache/generate errors.

**Impact:** confusing later failures instead of clear upload/processing failure.

**Fix:** Explicitly require `ACTIVE`. If state is `FAILED`, raise a clear error. If state is missing/unknown, continue polling or fail with an explicit message.

---

### M3. No total upload count/size budget before Gemini upload
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/fast_reader.py`  
**Flagged by:** GLM

Validation checks per-file max size but not total size or number of files. A user can select many large PDFs, causing long uploads, rate limits, cost spikes, and retries.

**Impact:** poor runtime/cost predictability for large batches.

**Fix:** Add preflight limits: max number of PDFs and max aggregate bytes. Auto-merge upfront or reject with clear guidance when the batch exceeds budget.

---

### M4. `validate_gemini_upload_size()` allows zero-byte files
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/document_prep.py`  
**Flagged by:** GLM

The check allows `size == 0` because it only tests `size <= max`.

**Impact:** empty converted or downloaded files can be uploaded to Gemini and fail later with confusing errors.

**Fix:** Require `0 < size <= GEMINI_MAX_UPLOAD_BYTES`; validate conversion outputs after LibreOffice/merge.

---

### M5. LibreOffice conversion timeout and error handling are brittle for large files
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/document_prep.py`  
**Flagged by:** Kimi

LibreOffice conversion timeout is fixed at 300s and `TimeoutExpired` is not converted into a user-friendly domain error.

**Impact:** large DOCX/XLSX tenders can fail after 5 minutes with unclear messages.

**Fix:** Make timeout configurable (e.g. 900s default for large tenders), catch `TimeoutExpired`, and raise a clear conversion timeout error naming the file.

---

### M6. `delete_remote_files()` hides persistent cleanup failures
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/gemini_client.py`  
**Flagged by:** Kimi

Remote delete exceptions are swallowed with only a warning.

**Impact:** persistent auth/network/quota problems can leak files indefinitely without actionable state.

**Fix:** Distinguish retryable/permanent errors. Record failed deletions in an orphan cleanup file/table and expose them in maintenance logs/admin checks.

---

### M7. Converted/merged PDFs accumulate in `fast_analysis/`
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/fast_reader.py`  
**Flagged by:** Qwen

Repeated analyses create converted PDFs, merged PDFs, metadata, and session files in `fast_analysis/`. Cleanup mostly targets session JSON/remote cache, not old converted/merged artifacts.

**Impact:** workspace grows over reruns.

**Fix:** At the start of a new analysis run, clean run-scoped generated files while preserving any valid session only if intentionally reused. Consider per-run subdirectories with retention policy.

---

### M8. `resolve_planos_pending()` can crash forever on missing/broken `audit_path`
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** DeepSeek

If external script writes a pending marker with a missing/bad `audit_path`, `read_text()` raises and the marker remains. Retrying hits the same broken marker.

**Impact:** stage-C analysis can get stuck on the same unresolved marker.

**Fix:** Validate `audit_path.exists()` before reading. Skip/log broken items and move bad markers aside or unlink in a controlled finally block.

---

### M9. `run_step1_deterministic()` / stage-C non-zero exits lack partial-artifact handling
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** Kimi

Non-zero exit codes other than `EXIT_VISUAL_PENDING` are treated as fatal, even if the external script produced partial artifacts useful for diagnosis or partial recovery.

**Impact:** opaque failures and unnecessary reruns when stage-C produced recoverable artifacts.

**Fix:** On non-zero exit, inspect expected artifacts and include their presence/paths in the error. Decide explicitly whether partial outputs are acceptable or must be discarded.

---

### M10. Merge fallback changes document context without explicit metadata
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/fast_reader.py`  
**Flagged by:** Qwen

When multi-PDF upload fails, the code merges PDFs and retries. Session metadata can still reference original sources while cache/upload paths point to the merged artifact.

**Impact:** later chat/session behavior may be confusing because the cached document differs from the original per-file upload shape.

**Fix:** Record `merge_fallback: true`, source ordering, and merged artifact path in session/meta. Regenerate prompt/bootstrap text to describe merged context.

---

### M11. `resolve_planos_pending(auto_leave)` writes low-confidence placeholders as if completed
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** Qwen

`auto_leave` writes JSON files with low confidence and limitations, then lets the pipeline continue as if planos analysis completed.

**Impact:** downstream stages may treat placeholder outputs as real analysis unless they explicitly inspect confidence/limitations.

**Fix:** Add a clear status field such as `analysis_status: "placeholder"`, propagate warning metadata to final artifacts/UI, or require downstream checks before treating it as completed.

---

### L1. Merged-PDF size error message is misleading
**Severity:** Low  
**File:** `apps/portal/seace_monitor/analysis/document_prep.py`  
**Flagged by:** GLM

If the auto-generated merged PDF exceeds Gemini's limit, the error tells the user to choose a smaller file even though the user did not directly choose that merged file.

**Fix:** Add context-aware error messages for merged artifacts: “Los documentos combinados exceden el límite; selecciona menos archivos o más pequeños.”

---

### L2. Planos schema version is hardcoded
**Severity:** Low  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** Kimi

`resolve_planos_pending()` writes `schema_version: "0.3"` without validating against the scripts pipeline's expected schema.

**Fix:** Centralize schema version in config/constant shared with the pipeline, or validate generated payloads against a schema.

---

## Recommended Fix Order

1. **C1/C2** — add per-process analysis/chat locking and atomic JSON writes.
2. **C3/H1/H2/M1/M6** — make Gemini remote resource ownership explicit; never lose cache/file handles; add orphan cleanup queue/sweep.
3. **H3** — store session paths relative to process dir and support archive/migration.
4. **H4/H6** — make reruns staged/atomic and eliminate stale field fallback.
5. **H5/M2** — standardize Gemini retry/state handling across all Gemini calls.
6. **M3/M4/M5/L1** — upload/conversion preflight and clearer errors.
7. **M8/M9/M10/M11/L2** — harden `tender_bridge` contracts and stage-C artifact semantics.
8. **M7** — workspace cleanup/retention.

## Notes

No security/hardening review was performed. Findings focus on correctness, reliability, state lifecycle, concurrency, external API behavior, artifact contracts, and maintainability.
