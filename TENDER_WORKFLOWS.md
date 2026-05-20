# tender_workflows

Monorepo unificado: monitoreo SEACE, pipeline documental (Paso 1) y workflows agenticos de licitación.

## Estructura

```
tender_workflows/
  apps/portal/           # Portal web + worker SEACE (seace_monitor)
  instrucciones/         # Runbook, prompts, schemas (workflow agentico)
  scripts/               # Pipeline determinístico (Paso 1, extractors)
  proyecto/              # Plantilla de expediente por licitación
  deploy/                # Docker Compose
  docs/                  # INTEGRATION.md, arquitectura
  config.example.yaml
  entities.csv.example
```

## Histórico

- Rama `archival/pre-restructure` y tag `v0.2-pre-workflows`: último estado solo `tender_procurement`.
- Repo archivado: https://github.com/rmichelena/tender_procurement

## Quick start (portal SEACE)

```bash
cp config.example.yaml config.yaml
cp entities.csv.example entities.csv
cd apps/portal && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=.
export TENDER_REPO_ROOT="$(git rev-parse --show-toplevel)"
cd ../..
python -m seace_monitor scan --once -v   # desde raíz, PYTHONPATH=apps/portal
python -m seace_monitor web
```

## Docker

```bash
cp config.example.yaml config.yaml && cp entities.csv.example entities.csv
docker compose -f deploy/docker-compose.yml up -d --build
```

## Documentación

- [README.md](README.md) — pipeline documental y extractores
- [docs/INTEGRATION.md](docs/INTEGRATION.md) — SEACE + Paso 1 + estados
