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
- Analysis steps 1.0–1.3 are deterministic; Google Gemini is used only for step 1.2b (plan/diagram pages).
- SEACE public UI uses JSF/PrimeFaces session state; ficha data requires POST with ViewState in the same session; process key is `(entity_id, nid_proceso)` with listado dedupe.
- ONGEI buscador `anio` URL param does not filter by year; monitor scan uses the first page only (~15 rows per active entity).
- Post-scan auto-descarte matches descripcion keywords (configurable list); older-year rows are kept to spot desierta and possible re-convocatoria; SEACE estado tracking (publicada/adjudicada/desierta/anulada) is planned.
- Cronograma column `fecha_consultas` is the start of presentación de consultas, not the absolución end date.
- Monitored entities live in gitignored `entities.csv` at repo root (59 ONGEI entities); config key `entities_csv`.
- VPS production deploy: `ssh bots-sysop`, Docker project `tender-workflows` under `~/tender_workflows/deploy/`, web UI at http://bots.infinitek.pe:8080/; SEACE egress via Squid `http://server.maczona.com:18081`.
- Portal UI views: `/publicaciones` = `publicada` only; `/analizados` = `analizada`/`portafolio`; `/descartados` = trash; default sort fecha de publicación desc; analizada rows use Ver (no Descartar on publicaciones); Portafolio only on analizados.
- Live SEACE ficha links use portal proxy `/seace/open/{process_id}` plus `/seace/p/...` (JSF session bootstrap, same as analyze)—not bookmarkable SEACE URLs or embedded HTML.
