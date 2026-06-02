# Branch Review — `ingest-plugin-contract`

Reviewed branch: `ingest-plugin-contract`
Head: `db43cde` — 17 commits on top of `origin/main`
Base: `01a1049`
Scope: +4047/-119 lines, 43 files changed

Reviewers:
- GPT-5.5 (via Z.ai fallback) — partial results, 3 findings before truncation
- Qwen 3.6 Plus (Fireworks) — full results, 4 findings
- DeepSeek V4 Pro (Fireworks) — output truncated, not usable
- GLM 5.1 (Z.ai) — failed

Effective reviewers: 2 (Qwen + GPT-5.5 partial)

## Summary

This is a large feature branch adding ADP Portal as a second input source, an ingest plugin contract (`SourceAdapter`), a feed layer with `FeedRepository` / `TenantFeedDecision`, SEACE republishing reconciliation, and a `lifecycle_phase` axis on `Process`.

The architecture is solid — the adapter contract, registry, and worker loop are well-structured. However, there are several findings around the worker's transaction boundary, scanner autoreject consistency, and ADP filename collisions that should be addressed.

## Findings

### High — Worker rolls back ALL adapter work on any single adapter failure

**File:** `apps/portal/seace_monitor/worker.py`
**Line:** ~130
**Reported by:** GPT-5.5

The worker loop runs all adapter scans, then all watchlists, inside a single try/except. If ADP scan succeeds (returns count) but SEACE watchlist raises, the `except Exception` block calls `session.rollback()`, undoing the ADP scan results and any intermediate state.

With the old code, SEACE scan + SEACE watchlist were the only operations and shared a commit — rollback was acceptable. Now with multiple independent adapters, one adapter's failure shouldn't lose another's work.

Trace:

1. Worker loop starts, ADP adapter scans and inserts 3 new processes.
2. SEACE adapter watchlist hits a network timeout.
3. `except Exception` → `session.rollback()` → 3 ADP processes lost.
4. Next cycle re-discovers them (no data loss due to content_hash dedup, but wasted work and potential transient duplicates).

Suggested fix:

Wrap each adapter call in its own try/except so failures are isolated:

```python
for adapter in adapters:
    if now >= next_scan_at[adapter.source]:
        try:
            scan_counts[adapter.source] = adapter.scan(cfg, session)
            session.commit()
            next_scan_at[adapter.source] = now + adapter.scan_interval_seconds(cfg)
            ran_any = True
        except Exception:
            session.rollback()
            logger.exception("Scan failed for %s", adapter.source)
```

---

### High — SEACE scanner re-applies autoreject to existing processes on every scan

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** ~230 (inside `_upsert_from_ficha`)
**Reported by:** Qwen

`_upsert_from_ficha` calls `apply_auto_reject_rules(proc, entity, self.auto_reject_rules)` unconditionally after upsert. This means a process that was previously `publicada` and unchanged (same `list_hash`, within TTL) still gets autoreject evaluated if it reaches the ficha refresh path. If the operator has added new rules since the last scan, existing `publicada` processes that previously passed can be autorejected.

Meanwhile, the ADP scanner only applies autoreject when `is_new=True`, creating an inconsistency between adapters.

Trace:

1. Process P is `publicada`, previously passed autoreject.
2. Operator adds rule `objeto:servicio limpieza`.
3. Next scan: P's `list_hash` hasn't changed, but `_needs_ficha_refresh` returns True (TTL expired).
4. `_upsert_from_ficha` runs, fetches ficha, updates process.
5. `apply_auto_reject_rules` evaluates new rules → P matches `limpieza` → autorejected.
6. User sees P disappear from publicaciones without manual action.

Suggested fix:

- Apply autoreject only on `is_new` consistently across all adapters, OR
- Apply autoreject on every scan but only when rules have changed since last evaluation (store `auto_reject_rules_hash` on the process).

---

### Medium — ADP downloader can silently skip documents with duplicate filenames

**File:** `apps/portal/seace_monitor/adp_downloader.py`
**Reported by:** Qwen

When downloading ADP documents, if two documents share the same `new_name` (e.g., both named "BASES.pdf"), the second download is skipped because the file already exists on disk. The second document's metadata is lost.

Suggested fix:

- Append a numeric suffix on collision (e.g., `BASES_2.pdf`), OR
- Include the document UUID in the filename to guarantee uniqueness.

---

### Medium — SEACE scanner ignores republishing of processes in terminal statuses

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** ~240
**Reported by:** Qwen

When a SEACE row matches an existing process in a terminal status (`descartada`, `archivada`), the scanner updates `last_seen_at` and continues. However, if that row is actually a **republished** process with a different `nid_proceso`, the republishing reconciliation logic (`adopt_republication`) never runs because the terminal-status guard fires first.

The `claimed_by_nomenclatura` map is built from non-terminal statuses, but the `proc = self.feed.find_by_ref(...)` lookup can return a terminal-status process. When `claimed is not proc` is False (no claimed process with that nomenclatura), the code falls through to the terminal guard instead of the republishing path.

Suggested fix:

Move the republishing/claimed check before the terminal-status guard, or include terminal-status processes in the `claimed_by_nomenclatura` map when their nomenclatura matches.

---

### Medium — `publicada` excluded from `_CLAIMED_STATUS_RANK`, breaking republishing detection

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** ~85-90
**Reported by:** Qwen

The `_CLAIMED_STATUS_RANK` dict and `_claimed_nomenclatura_map` may exclude `publicada` status, meaning republished processes that haven't progressed yet won't be detected as "claimed." This can cause the scanner to create a duplicate or overwrite metadata with a changed `nid_proceso` instead of adopting the republishing.

Suggested fix:

Ensure `publicada` is included in `_CLAIMED_STATUS_RANK` and `_claimed_nomenclatura_map` so that newly-published processes that haven't been downloaded yet are still tracked for republishing.

---

### Medium — `adopt_republication` can violate UniqueConstraint when mutating `source_ref`

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** ~130
**Reported by:** GPT-5.5

`adopt_republication` deletes an `existing` duplicate and then mutates `claimed.source_ref` to the new nid. If the `existing` row had the same `(source, entity_id, source_ref)` as the target, the delete frees it. But if another concurrent operation creates a row with that same identity between the `session.flush()` and the mutation, the `claimed.source_ref = row.nid_proceso` could violate the unique constraint.

In practice, this is unlikely in the single-worker model but worth defending against.

Suggested fix:

Wrap the adopt logic in a nested savepoint (already done at the call site) and ensure the delete+mutation is atomic within the same flush.

---

### Low — `lifecycle_phase` migration may conflict with existing data

**File:** `apps/portal/seace_monitor/db/models.py`

The new `lifecycle_phase` column has a default but no migration guard for existing rows. If the column is added to a non-empty table without a backfill, existing rows may have `NULL` until the next upsert.

Suggested fix: Add a backfill step that sets `lifecycle_phase` based on current `status` for existing rows.

---

## Quality Summary

The branch introduces significant architectural value:

- Clean `SourceAdapter` contract with registry pattern
- `FeedRepository` / `TenantFeedDecision` overlay for multi-tenant decisions
- ADP Portal as a second input source with its own scanner/parser/client/watchlist/downloader
- SEACE republishing reconciliation to avoid duplicates
- `lifecycle_phase` axis for pipeline tracking

The main risks are around the worker transaction boundary (cross-adapter rollback), autoreject consistency between adapters, and edge cases in the republishing reconciliation logic. These should be addressed before merge.

## Recommendation

Do not merge until:

1. Worker transaction isolation is fixed (per-adapter commit/rollback).
2. Autoreject application is consistent across SEACE and ADP adapters.
3. ADP filename collision handling is added.
4. Republishing detection covers terminal statuses and `publicada`.

The remaining Medium/Low items can be addressed in follow-up.
