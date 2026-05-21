## Learned User Preferences

- Communicate in Spanish for this project.
- Prefer Hermes gateway with an embedded chat UI (option 2) over building a custom agent/subagent system and web UI.
- Run agent workflows interactively via OpenClaw or Hermes on Telegram/Discord during development.
- Prefer a single monorepo (`tender_workflows`) over separate repos for the macro tender/procurement system.
- Want architectural decisions informed by long-term modularity (ingest adapters, workflow variants, bounded contexts) even when not building those features yet.
- Prefer preserving scroll position on portal list actions (analizar/descartar) after page refresh.

## Learned Workspace Facts

- Active GitHub repo is `rmichelena/tender_workflows`; former `tender_procurement` history lives on branch `archival/pre-restructure`.
- Local repo path: `/Users/roberto/Library/CloudStorage/Dropbox/sync-backup/data/DEVELOPMENT/mis developments/tender_workflows/`.
- Monorepo combines SEACE ingest/monitor, deterministic document analysis (pipeline steps 1.0–1.3), and agent phase (1.5+) defined in `instrucciones/`.
- Steps 1.0–1.3 in `instrucciones/` are deterministic; Gemini for 1.2b (plan/diagram pages). SEACE Monitor **analizar** uses a separate Gemini free-reader fast-path on user-selected PDFs (DOCX→PDF via LibreOffice first).
- SEACE public UI uses JSF/PrimeFaces session state; ficha data requires POST with ViewState; process key is `(entity_id, nid_proceso)`; live links use portal proxy `/seace/open/{process_id}` (not bookmarkable SEACE URLs).
- ONGEI buscador `anio` URL param does not filter by year; monitor scan uses the first page only (~15 rows per active entity).
- Post-scan auto-descarte matches descripcion keywords (configurable list); older-year rows kept for desierta/re-convocatoria; SEACE estado tracking planned.
- Cronograma list columns use **fin** of consultas and presentación propuestas stages (from cronograma_json when available).
- Monitored entities live in gitignored `entities.csv` at repo root (59 ONGEI entities); config key `entities_csv`.
- VPS production: `ssh bots-sysop`, project `tender-workflows` under `~/tender_workflows/deploy/`; hot-deploy via rsync `apps/portal/seace_monitor/` (use `web/templates/` and `web/static/` subpaths) then `docker compose -f docker-compose.vps.yml up -d --build web`; UI at http://bots.infinitek.pe:8080/; SEACE egress via Squid `http://server.maczona.com:18081`.
- Portal routes: `/publicaciones` (`publicada`, Descargar→`descargando`→`descargada`); `/descargados` (select PDFs, Analizar); `/analizados` (`analizada`/`portafolio`); `/descartados`; default sort fecha publicación desc.
- Background jobs (download/analyze): commit SQLite before long I/O (SEACE fetch, Gemini); HTTP handler commits+expunges after `descargando`/`running` so the request session cannot overwrite background writes; disabled HTML checkboxes are omitted from POST.
- Concurrent download/analyze per distinct process is supported (Starlette background threads, SQLite WAL); Gemini uploads retry with a fresh client on 503/upload-terminated; user prompt includes today's date (America/Lima).
