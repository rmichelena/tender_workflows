# Branch Review — `ingest-plugin-contract` vs `adp-input`

Reviewed branch: `ingest-plugin-contract` (63113a7)
Base: `adp-input` (38b2077)
Scope: +3122/-192 lines, 39 files, 16 commits

Reviewers:
- GPT-5.5 — completed
- GLM 5.1 (Z.ai) — completed
- DeepSeek V4 Pro (Fireworks) — completed
- Qwen 3.6 Plus (Fireworks) — aborted (timeout, no formatted output)

## Summary

This branch adds the ingest plugin contract layer on top of the ADP Portal integration in `adp-input`. The main additions are: `SourceAdapter` abstract base with registry, `FeedRepository` / `TenantFeedDecision` overlay, `lifecycle_phase` on `Process`, SEACE republishing reconciliation, and worker loop with per-adapter isolation.

The prior branch-review findings (against `main`) are all addressed in commit `63113a7`:
- ✅ Worker per-adapter commit/rollback isolation
- ✅ Autoreject consistency across adapters (only on `is_new`)
- ✅ ADP filename dedup handling
- ✅ Republishing detection for `publicada` and terminal statuses

No Critical or High findings remain. The remaining Medium findings are around autoreject apply durability and minor consistency issues.

## Findings

### Medium — `apply_autoreject_existing` lacks explicit commit and savepoint protection

**File:** `apps/portal/seace_monitor/web/settings_autoreject.py`
**Line:** ~60-80
**Reported by:** DeepSeek, GLM, GPT-5.5

The `apply_autoreject_existing` endpoint iterates over `publicada` processes, applies autoreject rules, and calls `session.flush()` for each match. Issues:

1. **No explicit commit**: The function depends on FastAPI's `get_db` auto-commit behavior at request end. If the auto-commit mechanism fails or is misconfigured, decisions are silently lost.
2. **No savepoint protection**: If flush fails on the Nth item (e.g., constraint violation), the entire transaction rolls back, losing all previous autoreject decisions from the same call. The scanner uses `begin_nested()` savepoints for individual row processing — this endpoint should too.

Trace:

1. User saves new autoreject rules and clicks "Aplicar a existentes".
2. Endpoint finds 5 matching processes, applies autoreject to items 1-4 successfully.
3. Item 5 hits a constraint error on `session.flush()`.
4. Entire transaction rolls back — items 1-4 are lost.
5. User sees "0 applied" or partial result on next page load.

Suggested fix:

```python
for proc in candidates:
    savepoint = db.begin_nested()
    try:
        match = apply_auto_reject_rules(proc, entity, rules)
        if match is not None:
            record_autoreject_decision(db, proc, ...)
            savepoint.commit()
            applied += 1
    except Exception:
        savepoint.rollback()
        logger.exception(...)
db.commit()
```

---

### Medium — `adopt_republication` always sets `last_seen_at` even when no merge happened

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** ~155
**Reported by:** GLM

`adopt_republication` unconditionally sets `claimed.last_seen_at = utcnow()` at the end, even when neither the nid adoption nor the existing-row deletion occurred (e.g., existing is not removable and nid doesn't advance). This means `last_seen_at` gets bumped for processes that weren't actually updated, which can affect TTL-based refresh logic.

Suggested fix: Only update `last_seen_at` when `source_ref` was actually mutated or `existing` was deleted.

---

### Medium — `once=True` now runs all adapters including watchlists

**File:** `apps/portal/seace_monitor/worker.py`
**Line:** ~120
**Reported by:** GPT-5.5

In the old code, `--once` ran a single scan cycle. Now the worker loop runs all adapters' scans AND watchlists in a single `--once` invocation. This is a behavioral change that makes `--once` significantly slower and more network-intensive, which may surprise operators using it for quick testing.

Suggested fix: Document the new behavior, or add `--scan-only` flag for testing.

---

### Low — `branch-review.md` is tracked in git

**File:** `branch-review.md`
**Reported by:** DeepSeek

The previous review file was committed and pushed. Should be removed before merge or excluded via `.gitignore`.

Suggested fix: `git rm branch-review.md` and add to `.gitignore`, or keep as documentation.

---

### Low — `.cursor/hooks/state/` files tracked in git

**File:** `.cursor/hooks/state/continual-learning*.json`
**Reported by:** DeepSeek

IDE state files shouldn't be versioned.

Suggested fix: Add `.cursor/` to `.gitignore`.

---

### Low — `adp_watchlist` bypasses `FeedRepository` seam

**File:** `apps/portal/seace_monitor/adp_watchlist.py`
**Reported by:** DeepSeek

The SEACE watchlist uses `FeedRepository` for process lookups, but the ADP watchlist queries the session directly. This is an architectural inconsistency — both should go through the same seam for consistency when the feed layer becomes the canonical access path.

Suggested fix: Refactor ADP watchlist to use `FeedRepository` in a follow-up.

---

### Low — `FeedRepository.query_by_status` returns raw SQLAlchemy `Query`

**File:** `apps/portal/seace_monitor/feed/repository.py`
**Reported by:** DeepSeek

The method returns a `Query` object instead of materialized results. This is a leaky abstraction — callers must know to call `.all()` or `.count()`. Acceptable as a transitional API but should be cleaned up.

Suggested fix: Return list or add explicit `query()` / `all()` variants.

---

### Low — `feed/decisions.py` missing trailing newline

**File:** `apps/portal/seace_monitor/feed/decisions.py`
**Reported by:** GLM, GPT-5.5

Cosmetic — file ends without newline.

---

## Prior Findings — Verification

All prior branch-review findings (against `main`) confirmed fixed:

1. ✅ **Worker rollback cross-adapter**: Now has per-adapter try/except with individual commit/rollback.
2. ✅ **SEACE autoreject on existing**: Both SEACE and ADP now only apply autoreject on `is_new`.
3. ✅ **ADP filename collision**: Uses `allocate_unique_path` with touch/unlink pattern.
4. ✅ **Republishing for terminal statuses**: `publicada` included in claimed map, republishing checked before terminal guard.
5. ✅ **`publicada` in `_CLAIMED_STATUS_RANK`**: Now included with rank 0.
6. ✅ **`adopt_republication` UniqueConstraint**: Uses nested savepoint at call site, delete+mutation within same flush.

## Final Recommendation

**Merge-ready after addressing the autoreject apply durability fix.** The `apply_autoreject_existing` endpoint should get savepoint protection and explicit commit before this ships to production — it's a real durability risk for batch operations.

The other Mediums (unnecessary `last_seen_at` bump, `--once` behavior) are safe to defer. Lows are cleanup.
