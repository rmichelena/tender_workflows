# TODO — follow-up post merge `multiple-inputs` → `main`

Merge: PR #2 (`285a50c`, 2026-05-28). Reviews: `review-2.md`, `review-3.md`, comentario final en PR #2.

## Operaciones inmediatas

- [ ] **VPS:** desplegar branch `0.3e-split-fisico` en `bots-sysop` (`git pull` + `docker compose --env-file .env -f docker-compose.vps.yml up -d --build` desde `deploy/`).
- [ ] **Smoke post-deploy 0.3e:** verificar que las listas descargados/analizados/archivados cargan correctamente (ahora leen de PipelineItem), detalle funciona, acciones (descartar/archivar/restaurar) operan, Ver en SEACE funciona.
- [ ] **Datos perdidos en incidente de migración (may-2026):** reprocesos que interesen no están en descargados/analizados; volver a **descargar** desde Publicaciones. Verificar que no queden procesos atascados en `descargando`.
- [ ] **Smoke post-deploy (general):** descarga multi-página de documentos (ej. `LP-ABR-1-2025-IGN-EV-1`, 6 docs), triage autoreject en `/descartados?estado=autorejected`, Ver en SEACE.

## 0.3e — Post-split cleanup (post-deploy, pre-merge a main)

- [ ] **Eliminar dual-write:** una vez validado en VPS, las escrituras pipeline van directo a PipelineItem (no via sync desde FeedItem). Eliminar `_sync_dirty_promoted`, el monkey-patch en `session_factory()`, y `pipeline_sync.py`.
- [ ] **Limpiar columnas pipeline de FeedItem:** eliminar del modelo `promoted_at`, `data_dir`, `watch_*`, `list_rank_*` y otras columnas que solo usa pipeline. Requiere migration SQLite (column rebuild).
- [ ] **Renombrar tabla física** `processes` → `feed_items` (opcional, bajo riesgo pero requiere migration).
- [ ] **Eliminar alias `Process = FeedItem`** en models.py.
- [ ] **Eliminar código legacy `autorejected`:** columnas `auto_reject_*`, status `autorejected`, rutas de compat.
- [ ] **Merge `0.3e-split-fisico` → `main`** tras validación VPS.

## PR #2 — Low deferidos (performance / limpieza)

- [ ] **`client.fetch_list_page`:** evitar GET extra de página 0 cuando el cliente ya tiene ViewState válido (~2N → N+1 requests en entidades grandes). Archivo: `apps/portal/seace_monitor/client.py`.
- [ ] **`Process._default_source_ref`:** hoy devuelve `""` si no hay ref; dos procesos non-SEACE del mismo entity podrían colisionar en `(source, entity_id, source_ref)`. Validar en adapter o rechazar insert sin ref. Archivo: `db/models.py`.
- [ ] **Índice redundante** `auto_reject_exempt` en `_ensure_sqlite_indexes` (ya `index=True` en modelo). Archivo: `db/session.py`.
- [ ] **Código muerto en `runner.py`:** revisar `_resolve_document_list`, `_reset_analysis_for_rerun` y similares tras refactor SEACE.
- [ ] **`repair_discarded_processes`:** no incluye `autorejected` (edge case; normalmente sin `data_dir`).

## Migración / deuda técnica SQLite

- [ ] Tras confirmar VPS estable con esquema nuevo: **eliminar** código de migración legacy (`processes_old` recovery, rebuild one-shot) según nota en `AGENTS.md`.
- [ ] Valorar **marcador de versión de schema** para backfills (`_backfill_*`) en lugar de checks en cada `init_db()` (review-3 Low).
- [ ] Documentar runbook si aparece `processes_old` huérfano o `foreign_key_check` fallido post-deploy.
- [ ] **Known limitation:** `commit_session_with_retry` rollback pierde dirty state → dual-write sync no corre en ese intento. Considerar `begin_nested()` (savepoints) para evitar full rollback (DeepSeek review finding).
- [ ] **Known limitation:** `_sync_dirty_promoted` no cubre `session.deleted` — si se elimina un FeedItem promovido (ej. `adopt_republication` dedup), PipelineItem queda huérfano. Considerar cleanup periódico.
- [ ] **Known limitation:** SQL directo (maintenance scripts) bypassa dual-write. Agregar sync explícito en rutas batch (`_flip_autorejected_status_to_overlay`, etc.) o periodic reconciliation.

## Producto / roadmap (sin urgencia)

- [ ] Integrar etapas **C–D** con portal (hoy solo A–B operativas).
- [ ] **Change Detection** como trigger externo (sin `source` propio aún).
- [ ] **SEACE estado** tracking (desierta, re-convocatoria, etc.) — planificado en roadmap.
- [ ] Segundo ingest adapter real (email, privado) además de `seace`.
- [ ] Settings autoreject: decidir si permitir YAML vacío (= deshabilitado) además de `enabled: false` por regla.

## Infra / calidad

- [ ] Añadir **pytest** al image Docker de prod o job CI dedicado (hoy tests no corren en contenedor VPS).
- [ ] Considerar **cola de descargas** (1 concurrente) o Postgres si SQLite sigue siendo cuello de botella bajo carga multi-usuario.
- [ ] Archivar o borrar rama local/remota `multiple-inputs` cuando ya no haga falta.

## Referencias

- PR mergeado: https://github.com/rmichelena/tender_workflows/pull/2
- Multi-ingest: `docs/INPUT_SOURCES.md`, `docs/STAGES.md`
- Reviews: `review-2.md`, `review-3.md`
