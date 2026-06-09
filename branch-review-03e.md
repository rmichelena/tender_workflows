# Multi-Review: Branch `0.3e-split-fisico` vs `main`

**Date:** 2026-06-09
**Branch:** `0.3e-split-fisico` (5 commits, +1026/-14 lines, 10 files)
**Reviewers:** GPT-5.5, GLM-5.1, DeepSeek V4 Pro
**Test status:** 311 passed, 2 skipped

## Summary

Feed/pipeline split refactor (0.3e-1→0.3e-3): creates `PipelineItem` table, dual-writes promoted Process changes to it, and flips pipeline list reads. The model/backfill path is solid. Main gaps are in the ID boundary between PipelineItem-backed lists and Process-backed routes, plus a couple of dual-write flush edges.

---

## Findings

### 🟡 HIGH — Pipeline lists emit PipelineItem IDs into routes that still expect Process IDs
**File:** `apps/portal/seace_monitor/web/list_pages.py:50`
**Reviewers:** GPT-5.5, GLM-5.1, DeepSeek

Templates receive `PipelineItem` objects but use `p.id` for routes like `/descargados/{id}`, `/analizados/{id}/estado`, `/processes/{id}/interest`. Those endpoints call `get_process_or_404()` querying `Process.id`. `PipelineItem.id` is NOT guaranteed to equal `origin_feed_id`.

The `process_id` property was added but is not yet used in templates/routes.

**Fix:** Update all pipeline route templates and endpoints to use `PipelineItem.process_id` (→ `origin_feed_id`) when constructing action URLs, or flip route handlers to accept PipelineItem IDs.

**Trace:** `Process(id=2)` promoted → `PipelineItem(id=1, origin_feed_id=2)`. Template renders `/analizados/1/estado` → `get_process_or_404(1)` loads wrong Process.

---

### 🟡 MEDIUM — `/archivados` route not flipped to PipelineItem reads
**File:** `apps/portal/seace_monitor/web/app.py:612-625`
**Reviewers:** GPT-5.5

`/archivados` has its own inline query against `Process` — it doesn't use `render_workflow_list`. This is the only pipeline list not flipped, and will diverge from pipeline_items as soon as Process rows are purged.

**Fix:** Route through `PipelineRepository` or `render_workflow_list`.

---

### 🟡 MEDIUM — AnalysisResult.pipeline_item_id missed when PipelineItem created in same sync pass
**File:** `apps/portal/seace_monitor/db/session.py:791`
**Reviewers:** GPT-5.5

`sync_to_pipeline` may create a new PipelineItem (pending in session.new). The subsequent `session.query(_PI).filter(origin_feed_id=...)` won't find it because `autoflush=False`. Result: PipelineItem created but `ar.pipeline_item_id` stays NULL.

**Fix:** Use the PipelineItem returned by `sync_to_pipeline()` directly instead of re-querying:
```python
pi = sync_to_pipeline(session, proc)
if pi is not None and obj.pipeline_item_id is None:
    obj.pipeline_item_id = pi.id
```

---

### 🟡 MEDIUM — SQLite retry doesn't cover dual-write flush errors
**File:** `apps/portal/seace_monitor/db/session.py:813`
**Reviewers:** GPT-5.5

`commit_session_with_retry` calls `_sync_dirty_promoted(session)` BEFORE the retry loop. If the sync's `session.flush()` hits a SQLite lock, the exception escapes without retry.

**Fix:** Move `_sync_dirty_promoted(session)` inside the `try` block:
```python
for attempt in range(attempts):
    try:
        _sync_dirty_promoted(session)
        session.commit()
        return
    except OperationalError as exc:
        ...
```

---

### 🟢 LOW — Dual-write runs on every commit (including feed-pure changes)
**File:** `apps/portal/seace_monitor/db/session.py:760-800`
**Reviewers:** GLM-5.1, DeepSeek

`_sync_dirty_promoted` runs on every `session.commit()` via monkey-patch, even when no promoted Process is involved. The early return (`if not candidates`) minimizes overhead, but the flush+scan still runs.

**Fix (optional):** Track whether any promoted Process was modified (e.g., via `@event.listens_for` attribute set) and skip sync entirely when not needed.

---

### 🟢 LOW — `PipelineItem.source` property not queryable
**File:** `apps/portal/seace_monitor/db/models.py:235`
**Reviewers:** GPT-5.5, DeepSeek

`PipelineItem.source` is a Python `@property` alias for `origin_source`. It works for template access but cannot be used in SQLAlchemy filters/sorts. Code that does `session.query(PipelineItem).filter(PipelineItem.source == ...)` will fail.

**Fix:** Use `PipelineItem.origin_source` in queries. Document the alias as template-only.

---

### 🟢 LOW — Backfill hardcodes `tenant_id = "default"`
**File:** `apps/portal/seace_monitor/db/session.py:705`
**Reviewers:** GLM-5.1

Acceptable for current single-tenant production data, but should be called out before multi-tenant backfill becomes real.

---

## Recommended Fix Order

1. **ID boundary** — use `process_id` (origin_feed_id) in templates/routes (blocks incorrect mutations)
2. **AnalysisResult sync** — use returned PipelineItem instead of re-query
3. **Archivados flip** — route through PipelineRepository
4. **SQLite retry coverage** — move sync inside retry loop
5. Low items are non-blocking

---

## Reviewer Notes

- **GPT-5.5:** 4 findings (1 High, 3 Medium). Thorough flush/lifecycle analysis.
- **GLM-5.1:** 3 findings. Good coverage on backfill and performance concerns.
- **DeepSeek V4 Pro:** 3 findings. Confirmed ID boundary and query alias issues.
- All reviewers converged on the ID boundary issue as the top finding.
- Test suite (311 passed) covers happy paths but lacks tests for ID divergence scenarios.
