## Learned User Preferences

- Communicate in Spanish for this project.
- Prefer Hermes gateway with an embedded chat UI (option 2) over building a custom agent/subagent system and web UI.
- Run agent workflows interactively via OpenClaw or Hermes on Telegram/Discord during development.
- Prefer a single monorepo (`tender_workflows`) over separate repos for the macro tender/procurement system.
- Want architectural decisions informed by long-term modularity (ingest adapters, workflow variants, bounded contexts) even when not building those features yet.
- Prefer preserving scroll position on portal list actions (analizar/descartar) after page refresh.
- Portal list views should show SEACE **objeto** and **descripción** (not Gemini markdown extracts like alcance/requisitos).
- Cronograma date columns in all list views use **fin** of consultas and presentación propuestas, not inicio.

## Learned Workspace Facts

- Active GitHub repo is `rmichelena/tender_workflows`; former `tender_procurement` history lives on branch `archival/pre-restructure`.
- Local repo path: `/Users/roberto/Library/CloudStorage/Dropbox/sync-backup/data/DEVELOPMENT/mis developments/tender_workflows/`.
- Monorepo combines SEACE ingest/monitor, deterministic document analysis (pipeline steps 1.0–1.3), and agent phase (1.5+) defined in `instrucciones/`.
- Steps 1.0–1.3 in `instrucciones/` are deterministic; Gemini for 1.2b (plan/diagram pages). SEACE Monitor **analizar** uses a separate Gemini free-reader fast-path on user-selected PDFs (DOCX→PDF via LibreOffice first).
- SEACE public UI uses JSF/PrimeFaces session state; ficha data requires POST with ViewState; process key is `(entity_id, nid_proceso)`; live links use portal proxy `/seace/open/{process_id}` (not bookmarkable SEACE URLs).
- ONGEI buscador `anio` URL param does not filter by year; monitor scan uses the first page only (~15 rows per active entity).
- Post-scan auto-descarte matches descripcion keywords (configurable list); older-year rows kept for desierta/re-convocatoria; SEACE estado tracking planned.
- Cronograma columns **Fin consultas** / **Fin presentación** use `fecha_fin` from `cronograma_json` (`extract_cronograma_fechas`; `fecha_inicio` fallback); list views use `ProcessListView` (no ORM mutation on render).
- Monitored entities live in gitignored `entities.csv` at repo root (user-maintained, not committed); config key `entities_csv`.
- VPS production: `ssh bots-sysop`, project `tender-workflows` under `~/tender_workflows/deploy/`; secrets in gitignored `deploy/.env` (from `deploy/.env.example`: `GEMINI_API_KEY`, `SEACE_HTTP_PROXY`); `cp config.example.vps.yaml config.yaml`; `docker compose -f docker-compose.vps.yml up -d --build`; UI at http://bots.infinitek.pe:8080/.
- Portal routes: `/publicaciones` (`publicada`, Descargar→`descargando`→`descargada`); `/descargados` (select PDFs, Analizar); `/analizados` (`analizada`/`portafolio`); `/descartados`; lists show objeto+descripción and fin-date columns; publicaciones default sort fecha publicación desc; descargados/analizados de-emphasize fecha publicación.
- Background jobs: commit SQLite before long I/O; concurrent download/analyze per distinct process (paid Gemini key, no global analyze lock); 503/upload retry with fresh Gemini client; system prompt anchors today (America/Lima) and avoids boilerplate future-legislation dudas; `normalize_legacy_filenames` must not suffix `_2` when dest already matches manifest `nombre`.
