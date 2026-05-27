# Multi-review — latest `main` commit

Review target: `rmichelena/tender_workflows` on `main`  
Commit reviewed: `4aa386686fc3237f50aa0d4503b7e419334fb38f`  
Commit title: `Add correlativo ordering and sortable columns to descargados/analizados lists`  
Scope: latest commit only, with surrounding code inspection.

Reviewers used:
- GPT-5.5
- DeepSeek V4 Pro
- GLM-5.1
- Qwen 3.6 Plus

Note: local `pytest` was unavailable in the review snapshot (`No module named pytest`), so this review is based on static code/diff inspection.

## Executive summary

The new `list_order.py` abstraction is small and mostly sound, but the integration has several runtime and workflow-state regressions. Two web actions can crash immediately, and the analysis state transition can leave new or re-analyzed items with missing or duplicate correlativos.

Recommended fix order:
1. Define/use the missing workflow redirect helper and fix the `descartar_analizado → archivar_analizado` call signature.
2. Fix analysis transitions so processes leave/enter list ranks in the correct order.
3. Preserve `sort`/`dir` in workflow action redirects.
4. Fix archived restore/repair paths that transition to `analizada` without assigning `list_rank_analizados`.

## Findings

### 1. Critical — `_workflow_list_redirect()` is called but never defined

- File: `apps/portal/seace_monitor/web/app.py`
- Line: `1013`
- Reviewers: GPT-5.5, DeepSeek, GLM, Qwen

`cambiar_estado_analizados()` commits a status change and then calls `_workflow_list_redirect(...)`, but no function with that name exists in the codebase. `workflow_list_query()` exists and is imported, but the wrapper redirect helper was never defined.

Trace:
1. User posts `/analizados/{id}/estado` to toggle `analizada ↔ portafolio`.
2. Route validates and commits `proc.status`.
3. Python evaluates `_workflow_list_redirect(...)`.
4. `NameError` → HTTP 500 after the DB change is already persisted.

Fix:

```python
def _workflow_list_redirect(
    path: str,
    *,
    sort: str = "",
    dir: str = "",
    scroll: str = "",
) -> RedirectResponse:
    return RedirectResponse(
        workflow_list_query(path, sort=sort, dir=dir, scroll=scroll),
        status_code=303,
    )
```

Or inline `RedirectResponse(workflow_list_query(...), status_code=303)` at the call site.

---

### 2. Critical — `descartar_analizado()` passes unsupported `sort`/`dir` kwargs

- File: `apps/portal/seace_monitor/web/app.py`
- Line: `991`
- Reviewers: GPT-5.5, GLM, Qwen

`descartar_analizado()` accepts `sort` and `dir` form values and forwards them to `archivar_analizado(...)`, but `archivar_analizado()` only accepts `process_id`, `background_tasks`, `db`, and `scroll`. This raises `TypeError` on every compatibility discard action for analyzed processes.

Trace:
1. User posts `/analizados/{id}/descartar`.
2. `descartar_analizado()` calls `archivar_analizado(..., scroll=scroll, sort=sort, dir=dir)`.
3. Python raises `TypeError: archivar_analizado() got an unexpected keyword argument 'sort'`.

Fix: either add `sort: str = Form("")` and `dir: str = Form("")` to `archivar_analizado()`, or avoid the direct Python call and delegate to a shared helper that accepts normalized redirect context.

---

### 3. High — newly analyzed processes do not get an `analizados` correlativo

- File: `apps/portal/seace_monitor/analysis/runner.py`
- Line: `185-188`
- Reviewer: GPT-5.5; verified against code

On first successful analysis, the process status is still `descargada` when `enter_analizados_list()` is called. `enter_analizados_list()` immediately returns unless the current status is `analizada`, `portafolio`, or `archivando`, so `list_rank_analizados` remains `NULL`. The code sets `process.status = analizada` only afterward.

Trace:
1. Process starts as `descargada`, `list_rank_analizados=None`.
2. Analysis succeeds.
3. `leave_descargados_list()` clears the descargados rank.
4. `enter_analizados_list()` runs while `process.status == descargada`, so it no-ops.
5. `process.status = analizada`.
6. The process appears in `analizados` with correlativo `—` / null rank.

Fix: set `process.status = ProcessStatus.analizada` before calling `enter_analizados_list()`, or change the rank helper to accept an explicit target list independent of current status.

---

### 4. High — re-analysis can preserve stale `list_rank_analizados` and create duplicate correlativos

- File: `apps/portal/seace_monitor/analysis/runner.py`
- Line: `157-159`, `185-188`
- Reviewer: DeepSeek; verified against code

When re-analyzing an already `analizada` process, the code changes it to `descargada` and enters the descargados list, but never calls `leave_analizados_list()`. The old `list_rank_analizados` remains stored while the process is temporarily outside `ANALIZADOS_LIST_STATUSES`. If other analyzed processes enter/leave and renumber during the analysis, the stale rank can collide when the process returns.

Concrete trace:
1. `P1(rank=1)`, `P2(rank=2)`, `P3(rank=3)` are `analizada`.
2. Re-analysis starts for `P2`: status becomes `descargada`, but `list_rank_analizados` stays `2`.
3. `P3` leaves or another process enters, causing `_renumber_list()` among currently analyzed rows. `P2` is excluded because status is `descargada`.
4. Another process can receive rank `2`.
5. `P2` succeeds; `list_rank_analizados is not None`, so `enter_analizados_list()` is skipped.
6. `P2` returns to `analizada` with stale rank `2`, duplicating another row.

Fix: before temporarily moving an analyzed process to `descargada`, call `leave_analizados_list(self.session, process)`. Then success can safely re-enter `analizados` with a fresh rank.

---

### 5. Medium — workflow list actions lose active `sort`/`dir` context

- File: `apps/portal/seace_monitor/web/app.py`
- Lines: `856-897`
- Reviewers: GPT-5.5, DeepSeek, GLM

The templates submit hidden `sort` and `dir` fields, but `descartar_descargado()` ignores them and `archivar_analizado()` does not accept them. Redirects only preserve `scroll`, sending users back to default correlativo ordering after actions.

Trace:
1. User opens `/descargados?sort=fecha_publicacion&dir=desc`.
2. User clicks discard; form includes hidden `sort`/`dir`.
3. Route ignores those fields and redirects to `/descargados` or `/descargados?scroll=N`.
4. User loses the active sort direction/column.

Fix: accept `sort` and `dir` in both routes, normalize them, and build redirects via `workflow_list_query(path, sort=sort, dir=dir, scroll=scroll)`.

---

### 6. Medium — `restore_archived_process()` early return can transition to `analizada` without rank

- File: `apps/portal/seace_monitor/process_storage.py`
- Line: `205-216`
- Reviewer: DeepSeek; related issue also noted by Qwen for repair path

If the archived process directory is missing, `restore_archived_process()` sets status based on whether completed analysis exists and returns early. When analysis exists, it sets `status = analizada` but does not call `enter_analizados_list()`, leaving `list_rank_analizados = NULL`.

Trace:
1. Archived process has `analysis.status == "done"`.
2. Its archived `data_dir` is missing, so `src is None or not src.is_dir()`.
3. Function sets `process.status = ProcessStatus.analizada` and returns.
4. Process appears in `analizados` without correlativo.

Fix: in the early-return branch, call `enter_analizados_list(session, process)` after assigning `analizada`; similarly call `enter_descargados_list()` if choosing `descargada` in future variants.

---

### 7. Low — `repair_archived_processes()` also transitions to `analizada` without rank

- File: `apps/portal/seace_monitor/process_storage.py`
- Line: `343-344`
- Reviewers: Qwen, DeepSeek

For archived processes missing their data directory but still having completed analysis, `repair_archived_processes()` clears download metadata and sets `status = analizada` without assigning `list_rank_analizados`. This is rarer than the restore path, but it creates the same null-correlativo state.

Fix: call `enter_analizados_list(session, proc)` after setting `proc.status = ProcessStatus.analizada`, or run/backfill ranks after repairs.

## Notes on filtered findings

- `_append_rank()` has a theoretical concurrent max-rank race if multiple workers mutate ranks simultaneously. I did not include it as a main finding because the current deployment model may serialize writes enough to make it less urgent, and the more immediate duplicate-rank bug above is concrete in normal re-analysis flow.
- Redundant `clear_list_ranks()` after `leave_descargados_list()` is harmless and not worth blocking on.

## Suggested tests to add

- FastAPI route test for `/analizados/{id}/estado` confirming no 500 and sort/dir/scroll preservation.
- FastAPI route test for `/analizados/{id}/descartar` confirming no `TypeError`.
- Integration test: first successful analysis of a `descargada` process assigns `list_rank_analizados`.
- Integration test: re-analysis of an existing `analizada` process leaves and re-enters `analizados` without duplicate ranks.
- `restore_archived_process()` missing-dir branch assigns rank when returning to `analizada`.
