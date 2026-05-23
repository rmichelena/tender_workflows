# Multi-Review v2: `apps/`

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `0be3396b1e83a45d3f77b305a9922b3a1a9b9e7b`  
**Scope:** `apps/` only  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Kimi K2.6, Qwen 3.6 Plus, GLM-5.1  
**Excluded by request:** security / hardening review

---

## Overall Assessment

The codebase is materially improved since the previous review. The earlier scanner savepoint, JSF form-action, ficha freshness, immutable list-view, and partial-download concerns appear to have been addressed or substantially reduced.

Remaining findings are mostly operational correctness issues around:

- existing SQLite database migrations,
- stale/competing background jobs,
- SEACE JSF state/link-id volatility,
- download recovery after crashes,
- list/date correctness edge cases.

No security/hardening findings are included.

---

## Findings

### H1. Existing SQLite databases are not migrated for new `Process` columns
**Severity:** High  
**File:** `apps/portal/seace_monitor/db/session.py`  
**Flagged by:** GPT-5.5

`init_db()` calls `Base.metadata.create_all()`, but `create_all()` does not alter existing tables. The code has migration/backfill logic for `entities`, but not for newly added `Process` columns such as `nid_sistema`, `reiniciado_desde`, `fecha_consultas`, `fecha_presentacion`, `cronograma_json`, `documentos_json`, and `ficha_url`.

**Impact:** existing deployments can fail at runtime with `OperationalError: no such column` when scanner/web code reads or writes new fields.

**Fix:** Add `_ensure_process_columns()` for every added `Process` column and call it from `init_db()`, or introduce Alembic migrations and run them before app/worker startup.

---

### H2. Stale analysis reruns can be overwritten by the old still-running job
**Severity:** High  
**File:** `apps/portal/seace_monitor/web/app.py` / `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** GPT-5.5

The analyze endpoint allows starting a new analysis when an existing `running` analysis is considered stale. It reuses/resets the same `AnalysisResult` row, but the old worker is not cancelled and there is no run token/generation check before applying results.

**Impact:** an old analysis job can finish after a rerun and overwrite the newer result/status.

**Fix:** Add an `analysis_run_id` / generation column or job table. Store the run id when scheduling, pass it to the worker, and before applying success/error verify the DB still contains that run id and status is still `running`. Discard stale worker results.

---

### H3. `descargado_detalle` crashes when `proc.analysis` is `None`
**Severity:** High  
**File:** `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** Qwen

For a freshly downloaded process that has never been analyzed, `proc.analysis` is `None`. The detail route accesses `proc.analysis.status` unconditionally.

**Impact:** visiting `/descargados/{id}` for a downloaded-but-not-analyzed process raises `AttributeError`.

**Fix:** Guard the access:

```python
if proc.data_dir and proc.analysis is not None and proc.analysis.status in ("running", "error"):
    ...
```

---

### H4. Worker startup continues after entity catalog sync failure
**Severity:** High  
**File:** `apps/portal/seace_monitor/worker.py`  
**Flagged by:** Qwen

`sync_entity_catalog_if_changed()` is called at worker startup inside a broad `try/except` that only logs and proceeds.

**Impact:** on a fresh deployment, if catalog sync fails, the scanner can run with zero entities and produce no results without a hard failure. On existing deployments, OSCE catalog updates are silently skipped until restart/manual action.

**Fix:** If the DB has no active entities, treat catalog sync failure as startup-fatal. Otherwise log an actionable warning and expose/admin a manual retry. Consider retry with backoff before entering the scan loop.

---

### H5. Scanner JSF ViewState can become stale after opening fichas during pagination
**Severity:** High  
**File:** `apps/portal/seace_monitor/scanner.py` / `apps/portal/seace_monitor/client.py`  
**Flagged by:** Kimi, Qwen

`SeaceClient` stores list form action/ViewState on the instance. Opening a ficha consumes/changes JSF state. If scanning continues to another page using cached list state, pagination or ficha POSTs can fail, repeat page 1, or miss pages.

**Impact:** multi-page scans can duplicate, skip, or fail rows depending on SEACE JSF state behavior.

**Fix:** After `open_ficha(row)`, refresh list form state before paginating. Better: make list page state an explicit context object returned from `fetch_list_page()` and consumed by `open_ficha()`, rather than mutable client-global fields. Add retry-on-ViewState-failure with fresh page fetch.

---

### M1. Stale `descargando` processes with partial download dirs never recover automatically
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/process_storage.py`  
**Flagged by:** DeepSeek

`repair_processes_missing_data()` resets `descargando` to `publicada` only when `data_dir` is missing. If the server crashes after creating `data_dir` but before finishing downloads, the partial directory remains and the process stays `descargando` forever.

**Impact:** stuck downloads require manual DB/file cleanup.

**Fix:** Add `recover_stale_downloads()` keyed by `download_started_at` or `updated_at` timeout. On recovery, clean partials, clear download metadata as needed, and reset status to `publicada`. Call it during app startup/lifespan.

---

### M2. Download failure rollback can leave stale `documentos_json` / `data_dir`
**Severity:** Medium  
**Files:** `apps/portal/seace_monitor/web/app.py`, `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** GLM, Qwen

If `_refresh_documentos()` updates `documentos_json` and a later download step fails, the failure handler resets status to `publicada` but may leave `documentos_json` and/or `data_dir` metadata from a half-failed attempt.

**Impact:** a later download attempt or UI render can use stale document metadata.

**Fix:** In the failure path, clear download metadata (`documentos_json`, `data_dir` if appropriate) or wrap metadata refresh and file fetch in a transaction/savepoint that rolls back on failure. Prefer committing `documentos_json` only after successful document resolution/download.

---

### M3. Stored `link_id` remains volatile and unsafe as a fallback
**Severity:** Medium  
**Files:** `apps/portal/seace_monitor/web/seace_view.py`, `apps/portal/seace_monitor/web/seace_proxy.py`, `apps/portal/seace_monitor/client.py`  
**Flagged by:** Kimi, GLM

The proxy correctly tries to re-resolve `link_id` from live SEACE HTML, but falls back to `process_row_from_model(process)` when the row is not found. Stored JSF `link_id` values are session/page-render scoped and can become stale.

**Impact:** fallback POSTs can fail or target the wrong JSF component when paired with fresh ViewState.

**Fix:** Treat stored `link_id` as a hint only. If live row resolution fails, do not attempt server-side open; fall back to client-side/manual navigation or re-query pages until the row is found.

---

### M4. Descending date sorts put blank/invalid dates before real dates
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/sorting.py`  
**Flagged by:** GPT-5.5

The date sort key uses a missing-date flag, but `reverse=True` for descending sorts reverses that flag too. Blank/invalid dates can appear before real dates.

**Impact:** publication lists prioritize incomplete rows for descending date sorts.

**Fix:** Keep missing-date ordering independent from direction, e.g. return `(missing, -timestamp)` for descending or split valid/invalid rows and append invalids last.

---

### M5. `row_snapshot_hash()` ignores persisted listing fields
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/parser.py`  
**Flagged by:** GPT-5.5

`row_snapshot_hash()` hashes only `fecha_publicacion`, `nomenclatura`, `descripcion`, and `cuantia`, while scanner persists additional fields such as `numero`, `reiniciado_desde`, `objeto`, `moneda`, `version_seace`, `nid_convocatoria`, `nid_sistema`, `link_id`, and `ntipo`.

**Impact:** changes to omitted fields can remain stale until ficha refresh — or indefinitely for statuses not refreshed.

**Fix:** Include all persisted `ProcessRow` fields that affect UI/workflow in the hash, or update listing fields independently even when ficha refresh is skipped.

---

### M6. Date filter skips processes with missing `fecha_presentacion` even when `fecha_publicacion` passes
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/scan_options.py`  
**Flagged by:** Qwen

In `since_date` mode, if `fecha_presentacion` is missing/unparseable, the filter returns `False` instead of falling back to `fecha_publicacion`.

**Impact:** processes with incomplete cronograma data are silently excluded even when their publication date matches the scan criteria.

**Fix:** If presentation date is missing, evaluate publication date before rejecting the process.

---

### M7. Background analysis task can still leave `running` if failure occurs before runner recovery
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** Qwen

The runner handles many exceptions by marking analysis `error`, but the outer background task mostly logs exceptions. If failure occurs before the runner reaches its internal recovery path, the analysis can remain `running`.

**Impact:** same user-visible stuck-running problem in earlier edge cases.

**Fix:** Add a top-level fallback in the background `_job` to mark analysis `error` and process status `descargada` on any uncaught exception, while preserving runner-level detailed error messages when available.

---

### M8. `process_data_dir()` truncation can compromise uniqueness in edge cases
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** Kimi

Directory names are built as `f"{process.nid_proceso}_{safe}"[:120]`. Truncating after concatenation can cut off uniqueness if the identifier is malformed/empty or if constraints change.

**Impact:** rare directory collisions or confusing paths.

**Fix:** Always preserve the full `nid_proceso`, truncate only the slug, and consider adding a short hash suffix.

---

### M9. Malformed `cronograma_json` is silently ignored
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/parser.py`  
**Flagged by:** Kimi

`fechas_listado_from_cronograma_json()` catches `JSONDecodeError` and falls back to empty data without logging.

**Impact:** corrupted DB data is hidden behind fallback dates; operators have no signal.

**Fix:** Log a warning with process id/context when JSON decode fails, and consider a maintenance check for corrupted cronograma fields.

---

### M10. `parse_ficha()` does not validate `ficha_id`
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/parser.py`  
**Flagged by:** Kimi

`ficha_id` is accepted and stored without validation.

**Impact:** if extraction fails or returns malformed data, later URL/cache logic may produce 404s or collisions.

**Fix:** Validate expected UUID/id format in `client.open_ficha()` or `parse_ficha()` and fail early with a clear error.

---

### L1. `descargando` is included in ficha refresh statuses
**Severity:** Low  
**File:** `apps/portal/seace_monitor/scanner.py`  
**Flagged by:** DeepSeek

The scanner refreshes fichas for processes in `descargando` after the freshness window, but does not resolve the download state.

**Impact:** stuck `descargando` rows can trigger repeated SEACE ficha requests every scan cycle.

**Fix:** Remove `descargando` from ficha refresh statuses, or treat old `descargando` as stale download recovery rather than normal ficha refresh.

---

### L2. Proxy session cache lacks TTL-based eviction
**Severity:** Low  
**File:** `apps/portal/seace_monitor/web/seace_proxy.py`  
**Flagged by:** GLM

The proxy keeps up to 200 `requests.Session` objects and evicts only on insertion above the cap; inactive sessions are not expired by age.

**Impact:** memory/connection-pool usage can grow and old users can lose sessions unpredictably when cap eviction occurs.

**Fix:** Use an LRU/TTL cache keyed by browser session id, or periodically evict sessions older than a configured TTL.

---

### L3. `parse_seace_date()` accepts ambiguous 2-digit years
**Severity:** Low  
**File:** `apps/portal/seace_monitor/scan_options.py`  
**Flagged by:** GLM

Fallback formats include `%y`, which applies Python's 2-digit year rules.

**Impact:** unlikely, but a malformed SEACE date like `26/01/50` can be interpreted as 2050.

**Fix:** Remove `%y` formats or add a sanity check such as `year >= 2000 and year <= current_year + N`.

---

### L4. `_assign_default_selection()` mutates UI dataclasses in-place
**Severity:** Low  
**File:** `apps/portal/seace_monitor/web/detail_data.py`  
**Flagged by:** Kimi

`_assign_default_selection()` mutates `ArchivoAnalizable.default_checked` directly.

**Impact:** currently likely harmless because objects are per-call, but brittle if reused/cached later.

**Fix:** Return copied dataclass instances or make the function explicitly pure.

---

## Recommended Fix Order

1. **H1** — add process table migration/backfill before runtime failures hit existing DBs.
2. **H2/H3/H4** — close user-visible stuck/crash paths in analysis detail, startup catalog sync, and stale reruns.
3. **H5/M3** — harden JSF ViewState/link-id lifecycle for multi-page scan and proxy fallback.
4. **M1/M2/L1** — add stale download recovery and clean rollback semantics.
5. **M4/M5/M6** — fix date/list correctness and stale listing invariants.
6. **M7-M10** — improve edge-case recovery/validation.
7. **L2-L4** — cleanup and defensive polish.

## Notes

No security/hardening review was performed. This review focuses on correctness, reliability, workflow behavior, persistence, UI correctness, performance/scalability, and maintainability.
