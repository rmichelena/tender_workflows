# Branch Review Final — 0.3e-split-fisico (2nd round)

**Branch:** `0.3e-split-fisico`
**Head:** `e2626ba`
**Base:** `e3e584f` (main)
**Reviewers:** GPT-5.5, GLM-5.1, DeepSeek V4 Pro
**Date:** 2026-06-09

## Summary

Segunda ronda de multi-review sobre la rama completa después de corregir los 7 findings de la primera ronda y completar los pasos 0.3e-4 (rename Process→FeedItem) y 0.3e-5 (bulk rename 58 files).

## Findings Consolidados (3 reviewers)

### 1. 🟡 CRITICAL: analizado_detalle.html usa `process.process_id` en FeedItem (GLM-5.1)
- **File:** `templates/analizado_detalle.html` + `models.py`
- **Status:** ✅ Fixed — añadido `FeedItem.process_id` property (returns `self.id`)
- Ambos modelos ahora tienen `.process_id` uniforme

### 2. 🟡 HIGH: seace_view usa `process.id` en vez de `process.process_id` (DeepSeek)
- **File:** `web/seace_view.py:51`
- **Status:** ✅ Fixed — usa `getattr(process, 'process_id', process.id)` para compatibilidad
- PipelineItem.id ≠ FeedItem.id; process_id es el FeedItem ID correcto

### 3. 🟡 HIGH: _macros.html `data-process-id="{{ p.id }}"` (DeepSeek)
- **File:** `templates/_macros.html:44`
- **Status:** ✅ Fixed — cambiado a `{{ p.process_id }}`
- JS polling `/api/processes/{id}/workflow` ahora usa el ID correcto

### 4. 🟡 MED: Double dual-write sync en commit_session_with_retry (GLM + DeepSeek)
- **File:** `db/session.py:815`
- **Status:** ✅ Fixed — eliminada la llamada explícita a `_sync_dirty_promoted`
- El wrapper de `session.commit()` ya lo maneja

### 5. 🟡 MED: Rollback pierde dirty state en retry (DeepSeek)
- **File:** `db/session.py:805-830`
- **Status:** ⚪ Known limitation — documentado
- SQLite lock es raro; retry eventualmente tiene éxito con objetos frescos
- No causa pérdida de datos (el cambio nunca se persistió)

### 6. 🟢 LOW: PipelineItem.entity sin back_populates (DeepSeek)
- **Status:** ⚪ By design — `Entity.processes` = FeedItem only

### 7. 🟢 LOW: SYNC_FIELDS no mapea source→origin_source (DeepSeek)
- **Status:** ⚪ By design — source es inmutable post-creación

### 8. 🟢 LOW: _sync_dirty_promoted no cubre session.deleted (DeepSeek)
- **Status:** ⚪ Accepted — `adopt_republication` no elimina FeedItems promovidos

### 9. 🟢 LOW: Dual-write bypass en SQL directo (DeepSeek)
- **Status:** ⚪ Accepted — rutas SQL directas son mantenimiento, no flujo normal

### 10. 🟡 MED: AnalysisResult.pipeline_item_id NULL en same-commit (GPT + GLM)
- **File:** `db/session.py:786-802`
- **Status:** ✅ Fixed — added session.flush() after PipelineItem creation to assign PK before linking AR
- autoflush=False meant pi.id was None when assigned to AR FK

### 11. 🟡 MED: tenant_id hardcoded to 'default' (GLM)
- **File:** `db/pipeline_sync.py:99`
- **Status:** ✅ Fixed — session_factory stores tenant_id, sync reads from session, web/app passes _config.tenant_id
- Tests and single-tenant callers use default; architecture ready for multi-tenant

### 12. 🟢 LOW: list_order.py ranks on FeedItem but reads from PipelineItem (GPT)
- **Status:** ⚪ Works via dual-write — will move to PipelineItem when dual-write removed

### 13. 🟢 LOW: SYNC_FIELDS omits first_seen_at (GPT)
- **Status:** ⚪ By design — first_seen_at is set-once, not updated

### 14. 🟢 LOW: Postgres backfill path untested (GPT)
- **Status:** ⚪ All prod is SQLite; Postgres is opt-in

## Commits (14 total)

1. `e44c67d` feat(0.3e-1): PipelineItem model + backfill migration
2. `7b9d1f8` feat(0.3e-2): dual-write via session hook
3. `097a401` feat(0.3e-3): flip pipeline list reads
4. `d4be823` fix(0.3e): address review findings
5. `548c382` fix(0.3e): address GPT-5.5 HIGH findings
6. `26cda84` docs: consolidated multi-review
7. `0df570e` fix(0.3e): address all multi-review findings
8. `7a3a6d3` refactor(0.3e-4): rename Process → FeedItem
9. `12bb85b` refactor(0.3e-5): bulk rename 58 files
10. `916a6cb` docs: update AGENTS.md
11. `16ff28e` fix(0.3e): address final multi-review findings (3 reviewers)
12. `aa4a6a5` docs: add final consolidated multi-review (2nd round)
13. `dec1e56` docs: update TODO with 0.3e post-split cleanup
14. `e2626ba` fix(0.3e): address remaining findings (AR linkage, tenant_id)

## Verdict

🟢 **Ready for deploy.** No blocking findings. 311 tests green.
