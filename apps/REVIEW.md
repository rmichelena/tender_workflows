# Multi-Review: `apps/`

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `c75da8dc4615144427bebfab653927dca2b89026`  
**Scope:** `apps/` only  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Qwen 3.6 Plus, GLM-5.1  
**Kimi K2.6:** timed out before returning findings  
**Excluded by request:** security / hardening review

---

## Overall Assessment

The `apps/` codebase is compact and readable, with a clear SEACE → DB → download → analysis → web UI flow. The highest-risk findings are not security-related: they are statefulness and workflow correctness issues around SEACE JSF pages, stale ficha data, transaction boundaries, and background jobs that can get stuck or leave partial state.

Priority should go to scanner/download/analysis reliability before UI polish.

---

## Findings

### C1. Entity-wide rollback on one process failure wipes scan progress
**Severity:** Critical  
**File:** `apps/portal/seace_monitor/scanner.py`  
**Flagged by:** DeepSeek

`_scan_entity()` processes all rows for one entity without per-process isolation. If a single `open_ficha()` / parse / upsert fails, the exception propagates to `run_once()`, which rolls back the session for the whole entity.

**Impact:** one transient SEACE failure can discard all successfully scanned processes and `last_seen_at` updates for that entity in the current cycle.

**Fix:** Add per-row `try/except` inside `_scan_entity()`. Persist successful rows independently, or use nested transactions/savepoints. Log failed row identifiers and continue.

---

### H1. Multi-page SEACE rows can open the wrong ficha
**Severity:** High  
**File:** `apps/portal/seace_monitor/client.py`  
**Flagged by:** GPT-5.5

`open_ficha()` performs a fresh GET of the listing page before posting the row action. For rows parsed from page 2+, the row component id / ViewState belongs to that later page, not the fresh first page.

**Impact:** scanning `max_pages > 1` can fail or open the wrong process ficha, corrupting cronograma/document data.

**Fix:** Preserve the page-specific form action/ViewState from `fetch_list_page()` and pass it into `open_ficha(row, page_state)`, or replay paginator state before posting the row action.

---

### H2. Scanner misses ficha-only updates to cronograma and documents
**Severity:** High  
**File:** `apps/portal/seace_monitor/scanner.py`  
**Flagged by:** GPT-5.5

Existing processes are refreshed only when `row_snapshot_hash(row)` changes. That hash excludes ficha-only fields such as `cronograma_json` and `documentos_json`.

**Impact:** SEACE can publish new documents or update cronograma dates while the listing row stays unchanged. The app then keeps stale documents/dates and downstream downloads/UI become wrong.

**Fix:** Periodically reopen fichas for active processes even when the listing hash is unchanged, or maintain a ficha refresh TTL/status window. Consider hashing ficha contents separately.

---

### H3. Analysis can remain `running` forever after crash/restart
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/runner.py`  
**Flagged by:** DeepSeek, Qwen

`analyze()` commits status `running` before the long pipeline. If the process is killed, OOMs, or restarts before completion/error handling, the DB row remains `running` indefinitely.

**Impact:** UI rejects re-analysis and manual DB intervention may be required.

**Fix:** Add startup recovery for stale `running` rows (`started_at` older than threshold → `error`/`interrupted`) or allow restarting stale running analyses from the web endpoint.

---

### H4. Download workflow can leave partial/inconsistent state
**Severity:** High  
**Files:** `apps/portal/seace_monitor/downloader.py`, `apps/portal/seace_monitor/analysis/runner.py`, `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** GPT-5.5, DeepSeek, Qwen

Downloads write directly to the final destination. If a stream fails midway, a partial file remains. Later retries see the path exists and can treat the corrupt file as complete. The background job also resets failed downloads back to `publicada` without a visible failure state, and intermediate commits can leave partially updated document metadata.

**Impact:** corrupt/incomplete PDFs can be analyzed; users may not see that a download failed.

**Fix:** Download to `*.part`, validate nonzero/content length where available, then atomically rename. Delete partials on exceptions. Add a `download_failed`/error field or visible failure message. Commit final `descargada` only after all required files succeed.

---

### H5. `_form_action()` can post to the wrong URL when JSF action is `.`
**Severity:** High  
**File:** `apps/portal/seace_monitor/client.py`  
**Flagged by:** Qwen

`urljoin(self.list_url, form["action"])` handles absolute/relative paths, but if JSF returns action `.`, `urljoin()` resolves to the containing directory rather than the original `.xhtml` page.

**Impact:** POSTs can fail or hit the wrong endpoint depending on SEACE's generated form action.

**Fix:** Special-case `action in ("", ".")` to return `self.list_url`. Keep `urljoin()` for normal relative/absolute actions.

---

### M1. Publicaciones sorting happens after an arbitrary 500-row limit
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** GPT-5.5, Qwen

The endpoint loads `q.limit(500).all()` and then applies `sort_processes()` in memory.

**Impact:** with more than 500 matching rows, the UI sorts only an arbitrary DB subset; rows that should appear first may be omitted.

**Fix:** Push sortable columns into SQL `ORDER BY` before `LIMIT`. For derived cronograma dates, persist normalized sortable fields or fetch all filtered rows only when bounded.

---

### M2. UI helper mutates ORM rows and may persist display recalculations
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/detail_data.py`  
**Flagged by:** GLM

`apply_fechas_listado()` rewrites `proc.fecha_consultas` and `proc.fecha_presentacion` on live SQLAlchemy entities used by web routes. The request session commits after yielding.

**Impact:** read-only page rendering can mutate persisted business data with presentation-layer recalculations.

**Fix:** Return view-model/DTO fields instead of assigning to ORM fields, or compute display values via properties without marking entities dirty.

---

### M3. `Process.objeto` is too short for SEACE business descriptions
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/db/models.py`  
**Flagged by:** GLM

`Process.objeto` is `String(64)`, but SEACE object descriptions often exceed 64 characters and are used for filtering/grouping.

**Impact:** stricter DBs may truncate/reject values; filters can become misleading or inconsistent.

**Fix:** Change to `String(256/512)` or `Text`, with migration/backfill as needed.

---

### M4. `publicaciones()` status filter mixes enum names and values
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/app.py`  
**Flagged by:** GLM

The route checks `estado in ProcessStatus.__members__` but later compares against `ProcessStatus.publicada.value`.

**Impact:** legitimate filters can be ignored if enum names and values diverge; the route relies on inconsistent representations.

**Fix:** Parse with `ProcessStatus(estado)` or validate against `{s.value for s in ProcessStatus}` and use that consistently.

---

### M5. `total_pages()` is dead code; scanner can silently miss pages
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/client.py`, `apps/portal/seace_monitor/scanner.py`  
**Flagged by:** DeepSeek

`SeaceClient.total_pages()` extracts paginator page count, but scanner loops over `range(self.config.max_pages)` and default `max_pages` is 1.

**Impact:** if SEACE returns multiple pages, processes beyond configured max are silently skipped with no warning.

**Fix:** On first page, call `total_pages(soup)`, scan `min(actual_pages, max_pages)`, and log a warning when actual pages exceed the configured cap.

---

### M6. `_parse_cronograma()` fallback can select the wrong table
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/parser.py`  
**Flagged by:** DeepSeek

If the specific cronograma table id selector misses, the parser scans all tables and accepts the first one with matching headers.

**Impact:** layout changes or similar tables can produce wrong `fecha_consultas` / `fecha_presentacion` values.

**Fix:** Add stronger guards: require multiple cronograma-like rows, validate date-like values, and prefer nearby labels/headings. Log ambiguous matches.

---

### M7. Invalid SEACE dates sort as Unix epoch
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/sorting.py`  
**Flagged by:** DeepSeek

`parse_seace_datetime()` returns `0.0` for unparseable dates.

**Impact:** malformed/placeholder dates sort as 1970 and jump to the top/bottom silently.

**Fix:** Return `float("inf")` / `float("-inf")` depending on sort policy, or return a tuple `(is_invalid, value)` so invalid dates consistently sort last. Log unexpected formats.

---

### M8. Dashboard template has malformed HTML
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/web/templates/dashboard.html`  
**Flagged by:** Qwen

The “Analizadas” card is missing a closing `</div>` for the numeric value.

**Impact:** browser repair may alter DOM structure and CSS layout.

**Fix:** Add the missing closing `</div>` after `{{ counts.get('analizada', 0) }}`.

---

### L1. Cronograma consultation date selection may pick the wrong stage
**Severity:** Low  
**File:** `apps/portal/seace_monitor/parser.py`  
**Flagged by:** Qwen

When multiple consulta-related stages exist, `extract_cronograma_fechas()` may choose the wrong consultation/presentation date depending on match order.

**Impact:** displayed deadline fields can be subtly wrong for complex cronogramas.

**Fix:** Prioritize exact stage names and use deterministic ranking for consultation/presentation stages; add fixtures for real multi-consulta examples.

---

## Recommended Fix Order

1. **C1** — isolate per-process scanner failures.
2. **H1/H2** — fix SEACE JSF page state and ficha freshness.
3. **H3/H4** — make background analysis/download recoverable and atomic.
4. **H5** — fix JSF form action edge case.
5. **M1/M5** — correct pagination and sorting behavior for larger datasets.
6. **M2/M3/M4** — data consistency and schema cleanup.
7. **M6/M7/L1** — parser/date robustness.
8. **M8** — template fix.

## Notes

No security/hardening findings were requested or included. Findings above focus on correctness, reliability, workflow behavior, persistence, UI correctness, performance/scalability, and maintainability.
