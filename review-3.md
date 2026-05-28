# Review 3 — `multiple-inputs`

Reviewed branch: `multiple-inputs`
Base: `origin/main` (`b9dd8ee455d73a6a4a2391d2bf8a2595ebc4e665`)
Head: `0a9f8877f2410759e3780da1d1e272e989a4722a`
Snapshot: `/tmp/tender_workflows_review3_snapshot`

Reviewers:

- GPT-5.5
- GLM 5.1 via Z.ai
- DeepSeek V4 Pro via Fireworks
- Qwen 3.6 Plus via Fireworks

## Summary

Six commits were added since review-2, specifically fixing the three blockers. All three are confirmed fixed by all four reviewers. The branch introduces new functionality (paginated ficha document fetching, SEACE search module extraction) that brings two new issues: a ViewState staleness bug in the new document pagination code, and a potential SQLite FK corruption during identity migration.

No Critical findings.

Recommended action before merge:

1. Fix the ficha document pagination ViewState issue (3/4 reviewers caught this independently).
2. Fix the SQLite migration FK integrity issue for existing databases.
3. The remaining Medium/Low items are safe to defer.

## Review-2 Blocker Verification

All three blockers are **confirmed fixed** by all four reviewers:

### ✅ HIGH — SEACE proxy cross-page ViewState

The old code mixed page-0 ViewState with later-page row data. Now replaced by `seace_search.py` module's `search_list_row_by_nomenclatura` which paginates 0→N with proper PrimeFaces AJAX, capturing fresh ViewState from each partial response. `open_ficha_for_process` uses the ViewState from the page where the row was actually found.

### ✅ MEDIUM — Process schema blocks non-SEACE inputs

`nid_proceso` is now `Mapped[str | None]` (nullable). The unique constraint is on `(source, entity_id, source_ref)`, not on `nid_proceso`. `_default_source_ref` falls back gracefully. Backfill handles legacy data.

### ✅ MEDIUM — Watchlist early guard before `_resolve_current_row`

The guard now checks `normalize_nomenclatura(process.nomenclatura)` instead of `nid_convocatoria/link_id`. Watchlist refresh uses `open_ficha_for_process` which resolves rows dynamically via the new search module.

## New Findings

### High — `collect_ficha_documentos` reuses stale ViewState across paginated requests

**File:** `apps/portal/seace_monitor/ficha_documents.py`
**Line:** ~35-40
**Reported by:** GPT-5.5, DeepSeek, GLM (3/4)

`collect_ficha_documentos` captures `javax.faces.ViewState` once from the initial ficha page HTML (line 35) and reuses the same value for all subsequent pagination POSTs (line 40). PrimeFaces AJAX responses include an updated ViewState in a `<update id="javax.faces.ViewState:0">` element. The sibling pagination code in `client.py` (`_partial_response_soup`) correctly extracts and updates ViewState from each partial response, but `ficha_documents.py` ignores it entirely.

If SEACE rotates ViewState between requests (standard JSF behavior), page 2+ will fail or return invalid data, causing downloads to miss documents on multi-page fichas.

The fix pattern already exists in `client.py`:

```python
view_state_update = xml.find("update", id=re.compile("ViewState"))
if view_state_update:
    self._list_view_state = view_state_update.get_text(strip=True)
```

Trace:

1. Download triggers `parse_ficha(... http_session=client.session, ficha_url=ficha.url)` → `collect_ficha_documentos(html, session, ficha_url)`.
2. Page 0 has 5 docs, total 12 → `total_pages=3`.
3. `view_state` captured from initial HTML as `VS0`.
4. Page 1: POST with `VS0` → partial response contains new ViewState `VS1` → ignored.
5. Page 2: POST still with `VS0` → SEACE may reject or return empty/error.
6. Documents 11-12 are not collected.

Suggested fix:

- Have `_fetch_documentos_page` return `(soup, updated_view_state)`.
- Parse the ViewState update from each partial XML response in `_partial_documentos_soup`.
- Use the updated ViewState for the next iteration.
- Mirror the pattern from `client.py._partial_response_soup`.

---

### High — SQLite rebuild can break `analysis_results` foreign key

**File:** `apps/portal/seace_monitor/db/session.py`
**Line:** ~291-300
**Reported by:** GPT-5.5 (1/4)

`_sqlite_rebuild_processes_table` executes:

1. `PRAGMA foreign_keys=OFF`
2. `ALTER TABLE processes RENAME TO processes_old`
3. Creates new `processes` table
4. `INSERT INTO processes SELECT ... FROM processes_old`
5. `DROP TABLE processes_old`
6. `PRAGMA foreign_keys=ON`

When SQLite renames `processes` to `processes_old`, it rewritea child-table foreign key definitions. `analysis_results.process_id REFERENCES processes(id)` becomes `REFERENCES "processes_old"(id)`. After `DROP TABLE processes_old`, the FK points to a non-existent table.

Any later insert/update on `analysis_results` that checks foreign keys will fail with `OperationalError: no such table: main.processes_old`.

Trace:

1. Existing SQLite DB has `analysis_results(process_id REFERENCES processes(id))`.
2. Migration: `ALTER TABLE processes RENAME TO processes_old`.
3. SQLite changes child DDL to `REFERENCES "processes_old"(id)`.
4. Migration drops `processes_old`.
5. Later `INSERT INTO analysis_results(process_id) VALUES (...)` raises `no such table: main.processes_old`.

Suggested fix:

- Recreate `analysis_results` as part of the migration (same rename→create→copy→drop pattern) to reset its FK definition.
- Or use a create-new pattern: create `processes_new`, copy data, drop old, rename new to `processes`, which avoids touching child FKs.
- Run `PRAGMA foreign_key_check` after migration to verify integrity.

---

### Medium — Restored/descarded autorejected items lose `estado` filter context

**File:** `apps/portal/seace_monitor/web/app.py`
**Line:** ~513, ~526
**Reported by:** GLM (also noted Low in review-2)

Both `restaurar` and `descartar_autorejected` redirect to plain `/descartados` without preserving the `estado` query parameter. When batch-triaging autorejected items at `/descartados?estado=autorejected`, each action drops the user back to the unfiltered view.

This was Low in review-2 but GLM argues Medium because the autoreject feature is specifically designed for batch triage, and this undermines that workflow.

Suggested fix:

- Accept `estado` as a hidden form field.
- Redirect to `/descartados?estado=<estado>` when present.

---

### Low — Backfill functions run on every `init_db()` with redundant double call

**File:** `apps/portal/seace_monitor/db/session.py`
**Line:** ~99-100
**Reported by:** Qwen (1/4)

`_backfill_process_sources` is called both from `init_db()` and again from `_migrate_process_identity_schema()`. Both have early-exit guards (`SELECT 1 ... LIMIT 1`), so it's cheap, but it adds unnecessary query noise on every boot.

Suggested fix: add a one-shot migration marker (schema version row) so backfills run exactly once per database lifetime.

---

### Low — `_parse_factor` with bare `-` causes `IndexError` instead of `ValueError`

**File:** `apps/portal/seace_monitor/auto_reject.py`
**Line:** ~116
**Reported by:** Qwen (1/4)

Query string `"-"` → `_parse_factor` pops `-` → recursive `_parse_factor` → `_parse_primary` → `IndexError` on `self.tokens[self.index]`. The validated settings UI won't hit this, but direct callers of `evaluate_query` would get an uncaught exception.

Suggested fix: add bounds check at top of `_parse_primary`: `if self.index >= len(self.tokens): raise ValueError("Expresión incompleta")`.

---

### Low — Dead code: `_prompt_path_for_process`

**File:** `apps/portal/seace_monitor/analysis/fast_reader.py`
**Line:** ~92
**Reported by:** DeepSeek, GLM (2/4)

Defined but never called. `_load_system_prompt` calls `_profiles_for_process` directly.

Suggested fix: remove or refactor to use it.

---

### Low — Empty autoreject rules YAML rejected with misleading "YAML inválido" message

**File:** `apps/portal/seace_monitor/web/settings_autoreject.py`
**Line:** ~48
**Reported by:** GLM, Qwen (2/4)

Deleting all rules produces valid YAML but the handler rejects it with "YAML inválido". The YAML is syntactically correct; the issue is semantic (no rules).

Suggested fix: either allow empty rules (disable autoreject), or change message to "Se requiere al menos una regla. Para deshabilitar, marque las reglas como `enabled: false`."

---

### Low — Bare auto-reject terms do not search `objeto`

**File:** `apps/portal/seace_monitor/auto_reject.py`
**Line:** ~64-70
**Carried forward from review-2**

`_Context.default` includes `descripcion + nomenclatura` but not `objeto`. Bare term `limpieza` won't match `objeto="Servicio de limpieza"` unless the word also appears in descripcion/nomenclatura. Design choice, but should be documented for rule authors.

Suggested fix: document that bare terms search only `descripcion + nomenclatura`, or add `objeto` to the default text.

## Final Recommendation

Do not merge until:

1. **`ficha_documents.py` ViewState propagation** is fixed — same class of bug that was fixed for list pagination, now present in the new document pagination. 3/4 reviewers caught it independently.
2. **SQLite migration FK integrity** is fixed — will break existing SQLite databases on migration.

The remaining Medium/Low items are safe to defer or fix in a follow-up.
