# Multi-review — `multiple-inputs` branch vs `main`

Review target: `rmichelena/tender_workflows`  
Branch: `multiple-inputs` at `97ac11580b4897ddd10b4587a96707a009eb559b`  
Base: `main` at `b9dd8ee455d73a6a4a2391d2bf8a2595ebc4e665`  
Scope: full branch diff, 34 files, +1621/-107

Reviewers:
- GPT-5.5 (via OpenAI) — completed
- GLM-5.1 (via zai) — completed
- DeepSeek V4 Pro (via OpenRouter) — completed
- Qwen 3.6 Plus — unavailable (Fireworks provider error)

## Executive summary

The branch introduces auto-reject rules, multi-source ingest adapters, workflow profiles, and a settings editor. The architecture is clean with good test coverage.

Key risks: auto-reject restore loop, query parser edge cases, YAML settings editor without input limits, and backfill running on every boot.

Recommended fix order:
1. Fix restore loop for autorejected processes.
2. Harden query parser: reject empty `_And([])`, unmatched parens, trailing `OR`, unknown fields.
3. Add size/rate limits to YAML settings editor.
4. Run backfill only once (migration marker).
5. Add Postgres adaptation for `interest_status` column.
6. Validate field names in queries.

## Findings

### 1. Critical — Restored autorejected processes get silently re-rejected

- File: `apps/portal/seace_monitor/web/app.py` line `483-498`
- Reviewers: GPT-5.5, GLM, DeepSeek (all three agree)

When a user restores an autorejected process via "Restaurar", the route sets status to `publicada` but does NOT clear `auto_reject_reason`. On the next scanner cycle, the same rule matches and the process is silently re-rejected. The user's restore is undone every scan cycle with no exemption mechanism.

Trace: Restaurar → `publicada` → scanner runs → `apply_auto_reject_rules` → match → `autorejected` again.

Fix: Clear `proc.auto_reject_reason = None` when restoring, and/or add an `auto_reject_exempt` flag.

---

### 2. Critical — `_And([])` evaluates to `True` — malformed queries match everything

- File: `apps/portal/seace_monitor/auto_reject.py` line `73-74`
- Reviewers: GPT-5.5, DeepSeek

`_And([]).evaluate()` returns `all([])` which is `True`. A trailing `OR` (e.g., `objeto:servicio OR `) or bare `()` produces `_And([])`, matching every process. A typo in the rules editor could silently auto-reject the entire pipeline.

Fix: Return `False` for empty `_And([])`, or raise `ValueError` in validation when a query parses to an empty node tree.

---

### 3. High — Unmatched opening parenthesis silently accepted

- File: `apps/portal/seace_monitor/auto_reject.py` line `155-162`
- Reviewer: DeepSeek

`_parse_primary` pops `(` but if the closing `)` is missing, the parser silently returns without error. `(objeto:servicio` passes validation.

Fix: After parsing inside `()`, if `_peek()` is not `)`, raise `ValueError("Unmatched parenthesis")`.

---

### 4. High — Unknown query field names silently return empty string

- File: `apps/portal/seace_monitor/auto_reject.py` line `47-58`
- Reviewer: DeepSeek

`_Context.fields` only defines `objeto`, `descripcion`, `nomenclatura`, `entidad`, `source`. Writing `cuantia:50000` silently returns `""` — the condition never matches but no error is raised.

Fix: Validate field names against `ALLOWED_FIELDS` at parse time and raise `ValueError` on unknown fields.

---

### 5. High — Query ending with `:` causes `IndexError`

- File: `apps/portal/seace_monitor/auto_reject.py` line `156`
- Reviewer: DeepSeek

`objeto:` → `_parse_factor` consumes `:` → recursive call → `_parse_primary()` with empty token list → `IndexError`.

Fix: Add bounds check at top of `_parse_primary`: `if self.index >= len(self.tokens): raise ValueError("Unexpected end of query")`.

---

### 6. High — YAML settings editor has no input size/rate limits

- File: `apps/portal/seace_monitor/web/settings_autoreject.py` line `41-52`
- Reviewers: GLM, DeepSeek

POST handler reads `rules_yaml` with no max size. A large payload causes excessive memory/CPU in the regex tokenizer and recursive parser.

Fix: Add max YAML size (e.g., 64KB), max rule count (e.g., 50-100 rules), max query length per rule.

---

### 7. High — Backfill runs on every `init_db()` call

- File: `apps/portal/seace_monitor/db/session.py` line `99`
- Reviewer: GLM

`_backfill_process_sources` and `_backfill_process_pipeline_fields` execute `UPDATE ... WHERE ... IS NULL` on every app startup. For large tables this is two full-table scans per boot.

Fix: Add a migration marker to run backfills only once, or check `SELECT COUNT(*) WHERE ... IS NULL` first.

---

### 8. Medium — `_Field` nesting drops outer field silently (`a:b:term`)

- File: `apps/portal/seace_monitor/auto_reject.py` line `97`
- Reviewer: GLM

`_with_field` has `if isinstance(node, _Field): return node` which ignores the incoming field. Nested field syntax silently drops the outer field.

Fix: Propagate the field or raise `ValueError` for nested field syntax.

---

### 9. Medium — `load_auto_reject_rules` re-serializes YAML just to filter `enabled`

- File: `apps/portal/seace_monitor/auto_reject.py` line `~195-218`
- Reviewers: GPT-5.5, GLM, DeepSeek (all three agree)

`yaml.safe_load` → filter → `yaml.safe_dump` → `validate_rules_yaml` → `yaml.safe_load` again. Wasteful double-parse that can alter quoting and loses comments.

Fix: Validate directly from the parsed dict without re-serializing.

---

### 10. Medium — `interest_status` column: Enum vs VARCHAR mismatch on PostgreSQL

- File: `apps/portal/seace_monitor/db/models.py` line `92`; `db/session.py` line `20`
- Reviewer: GLM

`interest_status` is mapped as `Enum(InterestStatus)` but the migration adds `VARCHAR(32) DEFAULT 'none'`. On PostgreSQL, `create_all` creates a native ENUM while `_ensure_table_columns` adds VARCHAR — type mismatch.

Fix: Add Postgres adaptation for this column type in `_adapt_column_type`.

---

### 11. Medium — `source_ref` default lambda may return `None` in bulk/raw contexts

- File: `apps/portal/seace_monitor/db/models.py` line `83-87`
- Reviewers: GLM, DeepSeek

The default lambda `context.get_current_parameters().get("nid_proceso")` returns `None` if `nid_proceso` is absent from insert parameters (possible in bulk inserts or non-SEACE sources).

Fix: Verify `nid_proceso` presence; for non-SEACE sources, make the default source-aware.

---

### 12. Medium — `descartar` routes don't handle `autorejected` status

- File: `apps/portal/seace_monitor/web/app.py` line `459`
- Reviewer: GLM

Autorejected processes appear in `/descartados` but can only be "restaurar"ed, not "descartar"ed (fully deleted). Attempting to descartar from another list gives an opaque 400.

Fix: Explicitly handle `autorejected` in descartar routes or document the state transitions.

---

### 13. Medium — `_load_system_prompt` reads profiles YAML twice per invocation

- File: `apps/portal/seace_monitor/analysis/fast_reader.py` line `~53, 85`
- Reviewer: GPT-5.5

`_prompt_path_for_process` parses `free_reader_profiles.yaml` once for path resolution, then `_load_system_prompt` parses it again for section block expansion.

Fix: Parse once and reuse the result.

---

### 14. Low — Unregistered sources silently fall back to SEACE prompt

- File: `apps/portal/seace_monitor/analysis/fast_reader.py` line `~53`
- Reviewer: GPT-5.5

If a new source is added without a corresponding profile, the SEACE-specific prompt is used with no warning.

Fix: Log a warning when no profile matches and `source != "seace"`.

---

### 15. Low — `validate_rules_yaml` return value unused by settings editor

- File: `apps/portal/seace_monitor/web/settings_autoreject.py` line `46-48`
- Reviewer: DeepSeek

The save handler validates but then writes raw user text to disk, discarding the validated representation. The return value of `validate_rules_yaml` is unused.

Fix: Intentional for preserving formatting, but worth documenting with an explicit assignment.
