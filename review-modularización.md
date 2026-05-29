# Review — `modularizacion`

Reviewed branch: `modularizacion`
Commit: `76bc30c` — "Refactor portal lists and workflow transitions into shared modules"
Base: `origin/main` (`fda1f5b`)
Scope: +551/-574 lines, 10 files changed

Reviewers: GPT-5.5, GLM 5.1 (Z.ai), DeepSeek V4 Pro (Fireworks), Qwen 3.6 Plus (Fireworks)

## Verdict: ✅ Clean refactoring — merge-ready

All four reviewers confirm the refactoring preserves behavioral equivalence. No Critical, High, or Medium findings remain after verification.

This is a well-executed extraction of duplicated code from `app.py` into focused modules:

- `web/document_routes.py` — shared document preview/download routes
- `web/list_pages.py` — shared workflow list rendering
- `web/process_queries.py` — shared `get_process_or_404` helper
- `web/workflow_transitions.py` — generic `run_status_transition_job` replacing 3 near-identical job patterns
- `web/static/workflow_list.js` — shared JS for scroll/lock/poll behavior
- `_macros.html` — shared `workflow_table` and `workflow_list_cells` macros

## Verification Summary

All five review dimensions confirmed clean:

1. **Behavioral regressions**: All route handlers, background jobs, error rollback paths, and JS behaviors (scroll restore, row locking, polling) preserved exactly. The generic `run_status_transition_job` with `work`/`rollback_status` parameters is equivalent to the original inline closures.

2. **Import/dependency issues**: All moved symbols correctly imported in new modules, unused imports cleaned from `app.py`.

3. **Route registration**: Document routes for `/descargados/` and `/analizados/` prefixes produce identical URL patterns. Registration order consistent.

4. **State/variable capture**: Closures in `app.py` capture `_config` at definition time — safe. `workflow_transitions.py` functions receive config as explicit parameter. No late-binding issues. `begin_download_transition` captures `proc.id` before `db.expunge()`.

5. **Template rendering**: `workflow_table` macro with `{% call %}` reproduces identical HTML. `ProcessStatus` and `InterestStatus` available as Jinja globals. Polling semantics equivalent (`stillInList` arrays match original inline functions).

## Findings

### Low — `get_process_or_404` with eager loads silently downgrades `MultipleResultsFound` to 404

**File:** `apps/portal/seace_monitor/web/process_queries.py`
**Line:** ~24
**Reported by:** Qwen

When `with_entity` or `with_analysis` is set, the function uses `.filter().one_or_none()` instead of `db.get()`. `one_or_none()` returns `None` for both missing and duplicate results, masking a potential data integrity issue with a 404. The original `db.get()` raises `MultipleResultsFound` on duplicate PKs.

In practice, duplicate PKs shouldn't occur (primary key), so this is a minor defensive-coding concern. The `one_or_none()` is needed for the joinedload path since `db.get()` doesn't support options in older SQLAlchemy.

Suggested fix: use `db.get()` for the simple case (already done) and `.filter().one()` for the eager-load case to preserve the exception on integrity issues.

### Low — `workflow_table` macro hardcodes `colspan="10"`

**File:** `apps/portal/seace_monitor/web/templates/_macros.html`
**Line:** ~35
**Reported by:** Qwen

The empty-message row uses `colspan="10"`, matching descargados and analizados (10 columns). Publicaciones has 11 columns and uses inline HTML. If the macro were later adopted for publicaciones, the colspan would be wrong.

Not an active issue — publicaciones still uses inline rendering.

Suggested fix: make `colspan` a macro parameter, defaulting to 10.

## Non-findings (reported but verified as false positives)

- **descargados `lockBtnSelector` missing** (Qwen): Verified present in head at line 31 — `lockBtnSelector: ".js-descartar-btn"` and `lockLabel: "Descartando…"`. False positive.
- **descargados polling behavior change** (GLM): The base used `stillInDescargados(status) = descargada || descartando`. Head uses `stillInList: ["descargada", "descartando"]`. Semantically identical. False positive.

## Final Recommendation

**Merge.** Clean refactoring with solid behavioral equivalence. The two Low items are safe to defer.
