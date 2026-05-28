# Review 2 — `multiple-inputs`

Reviewed branch: `multiple-inputs`  
Base: `origin/main` (`b9dd8ee455d73a6a4a2391d2bf8a2595ebc4e665`)  
Head: `eb3d8c26635dae778374f661d040636070b2ecd5`  
Snapshot: `/tmp/tender_workflows_review2_snapshot`

Reviewers:

- GPT-5.5
- GLM 5.1 via Z.ai
- DeepSeek V4 Pro via Fireworks
- Qwen 3.6 Plus via Fireworks

## Summary

The branch is much cleaner than the previous review. The earlier workflow-list, parser, restore-loop, YAML validation, and backfill issues appear fixed.

No reviewer reported Critical findings. The main remaining blocker-level risk is the SEACE proxy open flow: it can resolve a row on a later PrimeFaces page but still POST with the first page ViewState.

Recommended action before merge:

1. Fix the SEACE proxy cross-page ViewState issue.
2. Decide whether this branch is expected to support real non-SEACE runtime ingestion now. If yes, fix the schema identity model before merge; if not, document SEACE-only persistence as temporary.
3. Fix or explicitly accept the watchlist early-guard behavior for rows missing ficha metadata.

## Findings

### High — SEACE proxy can POST a later-page row with the first-page ViewState

File: `apps/portal/seace_monitor/web/seace_proxy.py`  
Approx line: `267`

`_try_server_open_ficha` extracts `javax.faces.ViewState` and form action from the initial `list_html` returned by the browser's GET request. It then calls `_row_for_open`, which may use `_resolve_current_row` to paginate and find the target process on a later SEACE page.

That means the code can combine:

- ViewState/form state from page 0
- `row.link_id`, `nidProceso`, `nidConvocatoria`, etc. from page N

For JSF/PrimeFaces, row action IDs and ViewState are tied to the current component/page state. Posting a later-page row action with page-0 ViewState can fail to redirect to `fichaSeleccion`, especially for processes not visible on the first page.

Trace:

1. Portal opens `/seace/open/<process_id>`.
2. Proxy GETs SEACE buscador page 0 and captures `ViewState=vs0`.
3. `_row_for_open` resolves the process on page 2, returning `link_id=fresh-link` and row identifiers from that page.
4. `_try_server_open_ficha` POSTs `{javax.faces.ViewState: vs0, fresh-link: fresh-link, ...}`.
5. SEACE may reject the action or fail to redirect to `fichaSeleccion`.
6. User gets the fallback notice instead of the ficha.

Suggested fix:

- Use the same `SeaceClient` state that resolved the row to open the ficha.
- Prefer calling `client.open_ficha(row)` after `_resolve_current_row`, then build the proxied location from that response URL.
- Add a regression test proving the POST uses the ViewState captured after pagination, not the original page-0 ViewState.

---

### Medium — Process schema still blocks real non-SEACE inputs despite `source` / `source_ref`

File: `apps/portal/seace_monitor/db/models.py`  
Approx line: `101`

The branch introduces `source` and `source_ref` as the apparent stable identity for multiple ingest sources, and the docs describe future inputs like email, manual references, private portals, and URLs.

However, `Process.nid_proceso` is still:

- `String(32)`
- non-null
- part of the only unique process identity constraint: `(entity_id, nid_proceso)`

That keeps the persistence model SEACE-specific. A real non-SEACE source must either fail because it has no `nid_proceso`, or copy an arbitrary external identifier into a 32-character SEACE field. Message-IDs, URLs, manual refs, or private portal IDs can exceed 32 chars and can also collide incorrectly under `(entity_id, nid_proceso)`.

Trace:

1. Runtime adapter creates `Process(source='email', source_ref='<long-message-id@example.com>', entity_id=1, anio=2026, nomenclatura='Specs')`.
2. No SEACE `nid_proceso` exists.
3. INSERT violates non-null `nid_proceso`.
4. If the adapter copies `source_ref` into `nid_proceso`, it can exceed 32 chars and still uses the wrong uniqueness model.

Suggested fix:

- Make `nid_proceso` nullable and SEACE-specific.
- Keep scanner validation requiring `nid_proceso` only for `source='seace'`.
- Add a canonical uniqueness constraint/index such as `(source, entity_id, source_ref)` or `(source, source_ref)`, depending on desired scoping.
- Update backfills and tests around identity resolution.

If non-SEACE ingestion is intentionally not runtime-ready in this branch, document that limitation clearly.

---

### Medium — Watchlist refresh returns early when ficha metadata is missing, before trying row resolution

File: `apps/portal/seace_monitor/watchlist.py`  
Approx line: `283`

`_refresh_watchlist_process` currently exits before attempting `_resolve_current_row` when either `process.nid_convocatoria` or `process.link_id` is missing:

```python
if not process.nid_convocatoria or not process.link_id:
    logger.warning(...)
    return False
```

But `_resolve_current_row` is exactly the mechanism that can recover fresh SEACE row metadata by searching with the entity/year/nomenclatura. If an older row or edge-case row lacks `link_id` / `nid_convocatoria`, watchlist refresh silently stops for that process and never attempts recovery.

Trace:

1. Process has `entity`, `anio`, and `nomenclatura`, but `link_id=''` or `nid_convocatoria=None`.
2. Watchlist refresh calls `_refresh_watchlist_process`.
3. Early guard returns `False`.
4. `_resolve_current_row` is never called.
5. The process remains stale indefinitely unless repaired elsewhere.

Suggested fix:

- Move `_resolve_current_row` before this guard.
- Or relax the guard to require only the fields needed for row resolution: `entity`, `anio`, and `nomenclatura`.
- After resolving, use the fresh row metadata for `client.open_ficha(row)`.

---

### Low — Restoring/confirming autorejected items loses the `estado` filter context

File: `apps/portal/seace_monitor/web/app.py`  
Approx lines: `514`, `525`

When the user is on `/descartados?estado=autorejected`, both actions redirect back to plain `/descartados`:

- `POST /descartados/{process_id}/restaurar`
- `POST /descartados/{process_id}/descartar`

This loses the filtered view after each action. It is not a data correctness issue, but it makes batch triage of autorejected rows clumsy.

Trace:

1. User opens `/descartados?estado=autorejected`.
2. User restores one autorejected process.
3. Route redirects to `/descartados`.
4. User lands on the combined discarded/autorejected list and must re-apply the filter.

Suggested fix:

- Include `estado` as a hidden form parameter.
- Redirect back to `/descartados?estado=<estado>` when present and valid.

---

### Low — Bare auto-reject terms do not search `objeto`

File: `apps/portal/seace_monitor/auto_reject.py`  
Approx lines: `64-70`

`_Context.default` is used for unqualified terms. It currently includes:

- `descripcion`
- `nomenclatura`

It excludes `objeto`. That means a bare rule like `limpieza` will not match a process whose `objeto` is `Servicio de limpieza` unless the same word also appears in `descripcion` or `nomenclatura`.

This may be intentional because the default rules use explicit fields, e.g. `objeto:servicio limpieza`. If so, it should be documented for rule authors.

Suggested fix:

- Either include `objeto` in the default searchable text,
- or document clearly that bare terms search only `descripcion + nomenclatura` and `objeto` must be queried explicitly.

---

### Low — Empty auto-reject settings are rejected with a confusing “YAML inválido” message

File: `apps/portal/seace_monitor/web/settings_autoreject.py`  
Approx line: `48`

If a user deletes all rules and saves, the YAML can be syntactically valid but semantically rejected because the handler requires at least one rule. The error message frames this as invalid YAML, which is confusing.

Suggested fix:

- Either allow an empty rules list to disable autoreject,
- or return a clearer message, e.g. `Se requiere al menos una regla. Para deshabilitar autoreject, marca las reglas como enabled: false.`

---

### Low — Dead code: `_prompt_path_for_process`

File: `apps/portal/seace_monitor/analysis/fast_reader.py`  
Approx line: `92`

`_prompt_path_for_process` appears to be defined but unused. This is not a runtime bug; it is cleanup.

Suggested fix:

- Remove the function,
- or refactor `_load_system_prompt` to use it if that was the intended design.

## Non-findings / resolved prior issues

The reviewers consistently found the prior review's major issues resolved:

- `_workflow_list_redirect` is defined.
- `sort` / `dir` / scroll context is preserved in workflow-list actions.
- First analysis and re-analysis ranking/correlativo transitions are fixed.
- `restore_archived_process` and `repair_archived_processes` call the list-entry helpers.
- Restored autorejected rows set `auto_reject_exempt=True` and clear `auto_reject_reason`.
- Auto-reject parser rejects empty expressions, unmatched parentheses, trailing operators/colons, unknown fields, and nested fields.
- YAML settings have size/count/query limits.
- Backfill checks for missing values before running updates.
- DeepSeek and Qwen did not report remaining Critical/High/Medium issues.

## Final recommendation

Do not merge until the SEACE proxy ViewState issue is fixed or explicitly accepted as a known limitation. The schema issue should also be fixed before treating non-SEACE ingestion as production-ready.

The remaining Low items are safe to defer.
