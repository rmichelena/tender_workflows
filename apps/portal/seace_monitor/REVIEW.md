# Multi-review — `multiple-inputs` branch vs `main`

Review target: `rmichelena/tender_workflows`  
Branch: `multiple-inputs` at `97ac11580b4897ddd10b4587a96707a009eb559b`  
Base: `main` at `b9dd8ee455d73a6a4a2391d2bf8a2595ebc4e665`  
Scope: full branch diff, 34 files, +1621/-107

Reviewers:
- GPT-5.5 — completed (5 findings)
- DeepSeek V4 Pro — unavailable (provider error, 0 tokens)
- GLM-5.1 — unavailable (provider error, 0 tokens)
- Qwen 3.6 Plus — unavailable (provider error, 0 tokens)

Note: All three Fireworks models failed to produce output for this review round (0 tokens, immediate return). This is a provider-level issue, not a diff/snapshot problem. Only GPT-5.5 delivered usable analysis.

## Executive summary

The branch adds solid infrastructure: auto-reject rule engine, multi-source ingest adapters, workflow profiles, and a settings editor. The code is well-structured with good test coverage.

The main actionable issue is a **restore loop for autorejected items** — user restore gets silently undone on the next scan cycle. Other findings are code quality and edge-case hardening.

Recommended fix order:
1. Add exemption mechanism for restored autorejected processes.
2. Harden `_And([])` empty-node evaluation in the query parser.
3. Eliminate YAML round-trip in `load_auto_reject_rules`.
4. Avoid double YAML parse in free reader profile resolution.
5. Add warning for unregistered source profiles.

## Findings

### 1. High — Restoring autorejected process creates immediate re-reject loop

- File: `apps/portal/seace_monitor/web/app.py`
- Line: `483-498`
- Reviewer: GPT-5.5; verified against code

The `restaurar` route restores autorejected processes to `publicada` (via `resolve_restore_status`), but does NOT clear `auto_reject_reason`. On the next scanner run, `_upsert_from_ficha` calls `apply_auto_reject_rules` on the restored `publicada` process, which matches the same rule and sets status back to `autorejected`. The user's restore action is silently undone every scan cycle with no way to exempt a specific process.

Trace:
1. Scanner upserts process → `apply_auto_reject_rules` sets `status=autorejected` and `auto_reject_reason`.
2. User clicks "Restaurar" in `/descartados`.
3. Route calls `resolve_restore_status` → `publicada` (no data_dir).
4. Route calls `clear_process_download_metadata` (unnecessary for autorejected).
5. Route sets `proc.status = publicada`.
6. Next scan cycle: `_upsert_from_ficha` → `apply_auto_reject_rules` → match → `autorejected` again.

Fix: When restoring from `autorejected`, either:
- (a) set a sentinel like `auto_reject_exempt=True` column that `apply_auto_reject_rules` checks, or
- (b) restore to a status like `descargada` that won't be re-processed by auto-reject, or
- (c) clear `auto_reject_reason` and add the process to an exemption list.

Also: the `restaurar` handler should not call `clear_process_download_metadata(proc)` for autorejected items that were never downloaded.

---

### 2. Medium — `_And([])` evaluates to `True` for empty/malformed queries

- File: `apps/portal/seace_monitor/auto_reject.py`
- Line: `73-74`
- Reviewer: GPT-5.5; verified against code

`_And([]).evaluate(ctx)` returns `all([])` which is `True` in Python. While `validate_rules_yaml` requires non-empty `query`, if a rule author writes a query that tokenizes to nothing (e.g., only parentheses `()`), the parser returns `_And([])` which always matches, creating a "reject everything" rule with no error.

Trace: Query `()` → tokens `['(', ')']` → `_parse_primary` pops `(` → `_parse_or` → `_parse_and` → peek `)`, loop doesn't enter → `_And([])` → `True`.

Fix: Make `_And.evaluate` return `False` for empty node lists (safer default for a filter engine), or add validation that rejects trivially-True parsed trees.

---

### 3. Medium — `load_auto_reject_rules` re-serializes then re-parses YAML

- File: `apps/portal/seace_monitor/auto_reject.py`
- Line: `~234`
- Reviewer: GPT-5.5; verified against code

To strip `enabled: false` rules, the function filters the parsed dict, calls `yaml.safe_dump` to re-serialize, then passes the result to `validate_rules_yaml` which re-parses. This YAML round-trip can alter query string representation in edge cases (quoting style changes). Any bug in validation would be masked by the different serialization.

Fix: Validate directly from the parsed list instead of re-serializing. Filter enabled rules, then validate each rule's `id`/`query` from the already-parsed data without the `safe_dump` → `safe_load` round-trip.

---

### 4. Medium — `_load_system_prompt` reads and parses profiles YAML twice

- File: `apps/portal/seace_monitor/analysis/fast_reader.py`
- Line: `~53, 68, 85`
- Reviewer: GPT-5.5; verified against code

`_prompt_path_for_process` reads and `yaml.safe_load`s `free_reader_profiles.yaml` to resolve the prompt path. Then `_load_system_prompt` may read and parse the same YAML file again to resolve `{{SECTIONS_BLOCK}}`. Both reads do full file I/O + YAML parse per invocation.

Fix: Read and parse the profiles YAML once, then use the result for both path resolution and section block expansion.

---

### 5. Low — Unregistered sources silently fall back to SEACE-specific prompt

- File: `apps/portal/seace_monitor/analysis/fast_reader.py`
- Line: `~53`
- Reviewer: GPT-5.5; verified against code

`_prompt_path_for_process` returns the default SEACE prompt when no profile matches the process source. If a new source is added without a corresponding profile, the free reader uses a contextually wrong prompt with no warning.

Fix: When no profile matches and `source != "seace"`, log a warning. Consider a generic fallback prompt.

---

## Notes

- **Fireworks provider issue:** All three Fireworks models (DeepSeek, GLM, Qwen) produced 0 tokens in both the original and retry runs for this review. This is a transient provider-level problem — all three worked correctly in the previous `main` review ~30 minutes earlier. The findings above come exclusively from GPT-5.5.
- **DB migrations:** The new columns (`source`, `source_ref`, `workflow_profile`, `interest_status`, `auto_reject_reason`) include backfill logic in `_backfill_process_sources` and appropriate indexes. The PostgreSQL adaptation from the previous review (`_adapt_column_type`) is used for the new columns. Looks correct.
- **Ingest abstraction:** The `ingest/` module is clean and minimal — protocol-based adapter with SEACE as the first implementation. Good extensibility pattern.
