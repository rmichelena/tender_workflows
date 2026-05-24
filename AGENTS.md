## Learned User Preferences

- Communicate in Spanish for this project.
- Prefer Hermes gateway with an embedded chat UI (option 2) over building a custom agent/subagent system and web UI.
- Run agent workflows interactively via OpenClaw or Hermes on Telegram/Discord during development.
- Prefer a single monorepo (`tender_workflows`) over separate repos for the macro tender/procurement system.
- Want architectural decisions informed by long-term modularity (ingest adapters, workflow variants, bounded contexts) even when not building those features yet.
- Prefer preserving scroll position on portal list actions (analizar/descartar) after page refresh.
- Portal list views should show SEACE **objeto** and **descripción** (not Gemini markdown extracts like alcance/requisitos).
- Cronograma date columns in all list views use **fin** of consultas and presentación propuestas, not inicio.
- Descartar from `descargada` fully deletes disk data and clears download/analysis metadata (`data_dir`, `documentos_json`, `AnalysisResult`) → `descartada`; Archivar from `analizada`/`portafolio` moves folder to tenant trash preserving PDFs and analysis → `archivada`; publicaciones Descartar only hides from list (no local data).
- `documentos_json` is populated only when downloading (fresh ficha fetch), not during scan.
- Design for multi-user: single deploy, `data/tenants/{tenant_id}/` (settings, seace, procesos, trash, agent); no Docker stack per user; Dropbox out of automated flow; see `docs/MULTI_TENANCY.md`.
- `instrucciones/` per-stage runbooks (not monolithic); design for multi-`source` ingest/entrypoints; **portafolio** without **analizar** uses per-source free reader (SEACE omits cronograma in docs); end-to-end vision in `vision/flujo_completo.md` only.

## Learned Workspace Facts

- Active GitHub repo is `rmichelena/tender_workflows`; former `tender_procurement` history lives on branch `archival/pre-restructure`.
- Canonical stages A→D in [docs/STAGES.md](docs/STAGES.md) (see also ARCHITECTURE, ROADMAP, MULTI_TENANCY, INTEGRATION, HERMES_VPS); `instrucciones/` organized per stage (`A_pre_portafolio`, `B_staging_portafolio`, `C_conversion`, `D_portafolio`, `shared/`, `vision/`); etapas C–D exist but not yet integrated with portal.
- Etapa C is hybrid deterministic+LLM (C.1–C.4, legacy steps 1.0–1.5b); SEACE **analizar** uses separate Gemini free-reader on selected PDFs (DOCX→PDF via LibreOffice); 1.2b planos via Gemini.
- SEACE public UI uses JSF/PrimeFaces session state; ficha data requires POST with ViewState; process key is `(entity_id, nid_proceso)`; **Ver en SEACE** uses portal proxy `/seace/open/{process_id}` (server-side list POST with fresh `link_id`/ViewState—stored row index goes stale; opens ficha redirect directly, not bookmarkable SEACE URLs).
- ONGEI buscador `anio` URL param does not filter by year; monitor scan uses the first page only (~15 rows per active entity).
- Post-scan auto-descarte matches descripcion keywords (configurable list); older-year rows kept for desierta/re-convocatoria; SEACE estado tracking planned.
- Cronograma columns **Fin consultas** / **Fin presentación** use `fecha_fin` from `cronograma_json` (`extract_cronograma_fechas`; `fecha_inicio` fallback); list views use `ProcessListView` (no ORM mutation on render).
- Entidades SEACE: catálogo OSCE descargado a tabla `Entity`; entidades activas para scan vía flag `activa` en Settings → entidades (UI); legacy `entities.csv` removed.
- `older_useful_material/` archives legacy `proyecto/` layout and historical review docs (not active pipeline).
- VPS production: `ssh bots-sysop`, project `tender-workflows` under `~/tender_workflows/deploy/`; secrets in gitignored `deploy/.env` (from `deploy/.env.example`: `GEMINI_API_KEY`, `SEACE_HTTP_PROXY`); `cp config.example.vps.yaml config.yaml`; deploy via `git pull` + `docker compose -f docker-compose.vps.yml up -d --build` (avoid rsync on tracked app files—causes drift vs `origin/main`); UI at http://bots.infinitek.pe:8080/.
- Portal routes `/publicaciones`…`/descartados`; process files under `data/tenants/{tenant_id}/procesos/` (archived in `.../trash/`); legacy `data/procesos/` auto-migrates on startup—update DB `data_dir` before orphan cleanup or repair may delete migrated folders.
- Background jobs: commit SQLite before long I/O; multi-entity scanner commits per entity; per-process DB savepoints in scanner (one ficha failure must not roll back whole entity); concurrent download/analyze per distinct process (paid Gemini key, no global analyze lock); 503/upload retry with fresh Gemini client; system prompt anchors today (America/Lima) and avoids boilerplate future-legislation dudas; `normalize_legacy_filenames` must not suffix `_2` when dest already matches manifest `nombre`.
