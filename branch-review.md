# Branch Review — `ingest-0.3c-autoreject-overlay` vs `main`

**Branch:** `ingest-0.3c-autoreject-overlay` (2d2b800)
**Base:** `main` (d7aa7ed)
**Scope:** 13 commits, +1931/-140, 31 files

**Reviewers:**
- GPT-5.5 → failover a GLM 5.1 (Z.ai) — findings included
- GLM 5.1 (Z.ai) — ✅ completed
- DeepSeek V4 Pro (Fireworks) — ✅ completed
- Qwen 3.6 Plus (Fireworks) — ❌ billing cooldown

---

## Summary

This branch introduces the autoreject overlay pattern in 4 micro-steps (0.3c-1 through 0.3c-3), then adds feed→pipeline promotion marks (0.3d), watchlist adaptive TTL, and changelog TZ fixes. It also addresses all prior review findings (claimed map dedup, flip migration, restore race condition, cross-tenant overlay purge).

The architecture is sound: bi-régime reads with idempotent migration, overlay exempt-supersede for race protection, `promoted_at` as one-way latch for dedupe defense-in-depth, and adaptive watchlist TTL with configurable thresholds.

**One High confirmed**, otherwise Mediums and Lows.

---

## Findings

### High — `ZoneInfoNotFoundError` no importada → NameError en runtime

**File:** `apps/portal/seace_monitor/watchlist_changelog.py`
**Line:** 268
**Reported by:** DeepSeek

La línea 7 importa `from zoneinfo import ZoneInfo` pero la línea 268 usa `ZoneInfoNotFoundError` en un `except` sin importarlo. Si el config tiene un timezone inválido, Python lanza `NameError: name 'ZoneInfoNotFoundError' is not defined` en vez de entrar al handler — crashea todo el changelog rendering.

**Verificado en código:** ✅ confirmado. Solo `ZoneInfo` está importado.

**Fix:**
```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
```

---

### Medium — `apply_auto_reject_rules` solo consulta legacy `auto_reject_exempt`, no el overlay

**File:** `apps/portal/seace_monitor/auto_reject.py`
**Line:** ~305
**Reported by:** GLM (2 sesiones)

Después del flip (0.3c-3), la guarda es `if process.auto_reject_exempt: return None` — lee solo la columna legacy. Si el campo legacy está stale (migration glitch, race con web handler), la única protección es el `_upsert_decision` exempt-supersede que bloquea la escritura. No es destructivo pero es un gap de defense-in-depth.

**Fix:** Agregar check del overlay: `FeedRepository(session).decision_for(process) == DECISION_EXEMPT` antes de evaluar reglas, o aceptar el riesgo actual.

---

### Medium — Watchlist TTL: carga todos los procesos en Python en vez de filtrar en SQL

**File:** `apps/portal/seace_monitor/watchlist_refresh.py` / `watchlist.py`
**Line:** 92 / 189
**Reported by:** GLM (2 sesiones), DeepSeek

El adaptive TTL requiere parsear `cronograma_json` por proceso (Python-side), así que se carga `.all()` y filtra en Python. Con cientos/miles de watchlisted items, materializa todo en memoria cada ciclo. El caso base (deadline lejano, TTL estándar) podría filtrarse en SQL primero.

**Fix:** Hybrid: filtrar `watch_checked_at < now - base_interval` en SQL primero, luego refinar en Python solo para los candidatos.

---

### Medium — Watchlist adaptive TTL no mantiene urgencia post-deadline

**File:** `apps/portal/seace_monitor/watchlist_refresh.py`
**Line:** 54
**Reported by:** GLM

`_has_urgent_cronograma_deadline` chequa `now_lima <= dt <= deadline`. Una vez que pasa el deadline, la urgencia cae instantáneamente al TTL base (3h). En realidad, justo después del deadline es cuando se publican resultados/buena pro — aún debería ser urgente por una ventana.

**Fix:** Agregar look-back window (e.g., mantener urgente por `watchlist_urgent_horizon` horas después del deadline), o documentar como intencional.

---

### Medium — `effective_autorejected_ids()` llamado múltiples veces por request sin caché

**File:** `apps/portal/seace_monitor/web/app.py`
**Line:** 285, 322, 513
**Reported by:** GLM

Cada ruta (dashboard, publicaciones, descartados) llama `effective_autorejected_ids()` que hace un outer join Process × TenantFeedDecision. Para single-tenant SQLite actual es aceptable, pero para multi-tenant futuro será N+1.

**Fix:** Cachear en request scope (`g.autorejected_ids` en FastAPI dependency).

---

### Low — `_flip_autorejected_status_to_overlay` ejecuta SELECT guard en cada `init_db`

**File:** `apps/portal/seace_monitor/db/session.py`
**Line:** 298
**Reported by:** GLM (2 sesiones)

El guard `SELECT 1 FROM processes WHERE status = 'autorejected' LIMIT 1` es eficiente (indexed) pero corre en cada boot forever sin flag persistente de migración completada.

**Fix:** Considerar tabla de metadata de migraciones one-shot. No blocking.

---

### Low — `_PROMOTED_STATUSES` en `db/session.py` usa strings crudos que pueden diverger del enum

**File:** `apps/portal/seace_monitor/db/session.py`
**Line:** 526
**Reported by:** GLM

El tuple `_PROMOTED_STATUSES` tiene strings crudos (`"descargando"`, etc.) mientras `feed/promotion.py` usa el enum `ProcessStatus`. Si se renombra un status, este tuple queda stale silenciosamente.

**Fix:** Importar desde `promotion.py` o derivar del enum:
```python
from ..feed.promotion import PROMOTED_STATUSES as _PROMOTED_SET
_PROMOTED_STATUSES = tuple(s.value for s in _PROMOTED_SET)
```

---

### Low — Items autorechazados nuevos no se promueven → overlay es su única protección en dedupe

**File:** `apps/portal/seace_monitor/scanner.py`
**Line:** 519-528
**Reported by:** DeepSeek

Un item nuevo que matchea autoreject recibe overlay decision pero no `promote()` — y no debería, porque es feed-puro. Si el overlay se borra (SQL directo o API futura), el item queda como `publicada` sin `promoted_at`, elegible para dedupe deletion. Edge case de baja probabilidad con la API actual.

**Fix:** Documentar la dependencia. Si se agrega endpoint de "delete overlay decision", agregar guarda con `promoted_at`.

---

### Low — `changelog_entry_at_label` usa `datetime.min` con tzinfo=Lima como fallback

**File:** `apps/portal/seace_monitor/watchlist_changelog.py`
**Line:** 250-253
**Reported by:** DeepSeek

Funcional pero inusual. Para fechas inválidas, el fallback es `datetime.min.replace(tzinfo=LIMA)` (año 1, offset -5:00).

**Fix:** Reemplazar con `datetime.min.replace(tzinfo=timezone.utc)` para claridad. Baja prioridad.

---

## Prior Review Findings — Verification

Todos los findings previos del review de `0.3c` (GPT-5.5 + GLM) están confirmados como corregidos:

1. ✅ **Claimed map duplicates** — dedup por ID antes de agregar al map
2. ✅ **Flip migration misses exempt items** — ahora flipea todos los `status=autorejected`
3. ✅ **Scanner + restore race condition** — exempt-supersede en `_upsert_decision`
4. ✅ **Cross-tenant overlay purge** — `_purge_orphan_feed_decisions` en cleanup
5. ✅ **Autoreject apply durability** — savepoints en batch apply
6. ✅ **`auto_reject_exempt` dual-write** — documentado como transicional

---

## Final Recommendation

**Merge-ready tras fix del import de `ZoneInfoNotFoundError`.** Es un bug real que crashea el changelog con cualquier timezone inválido en config — una línea de fix.

Los Mediums de watchlist TTL (performance y post-deadline urgency) son mejoras postergables. El Medium de `auto_reject_exempt` guard es defense-in-depth, no destructivo. Los Lows son cleanup.
