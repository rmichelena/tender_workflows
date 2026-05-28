# TODO — follow-up post merge `multiple-inputs` → `main`

Merge: PR #2 (`285a50c`, 2026-05-28). Reviews: `review-2.md`, `review-3.md`, comentario final en PR #2.

## Operaciones inmediatas

- [ ] **VPS:** desplegar `main` en `bots-sysop` (`git pull` + `docker compose --env-file .env -f docker-compose.vps.yml up -d --build` desde `deploy/`).
- [ ] **Datos perdidos en incidente de migración (may-2026):** reprocesos que interesen no están en descargados/analizados; volver a **descargar** desde Publicaciones. Verificar que no queden procesos atascados en `descargando`.
- [ ] **Smoke post-deploy:** descarga multi-página de documentos (ej. `LP-ABR-1-2025-IGN-EV-1`, 6 docs), triage autoreject en `/descartados?estado=autorejected`, Ver en SEACE.

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
