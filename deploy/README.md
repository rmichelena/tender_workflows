# Deploy — SEACE Monitor

## Compose files

| Archivo | Uso |
|---------|-----|
| `docker-compose.yml` | **Default SQLite** — web + worker, sin Postgres |
| `docker-compose.postgres.yaml` | Overlay opt-in: añade servicio `db` + `DATABASE_URL` |
| `docker-compose.vps.yml` | Producción VPS (`cd deploy/`, volumen `tender_data`) |

### Desde la raíz del repo (SQLite)

```bash
cp config.example.yaml config.yaml
cp deploy/.env.example deploy/.env   # GEMINI_API_KEY, SEACE_HTTP_PROXY
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

`config.yaml` debe usar SQLite, p. ej. `database_url: sqlite:////app/data/seace.db`.

### Postgres opt-in

```bash
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.postgres.yaml up -d --build
```

Descomenta o define `DATABASE_URL` en `deploy/.env` si quieres sobreescribir el default del overlay.

### VPS

```bash
cd deploy
cp config.example.vps.yaml config.yaml
cp .env.example .env
docker compose --env-file .env -f docker-compose.vps.yml up -d --build
```

## Base de datos

- **SQLite y Postgres son backends independientes.** `init_db()` crea el schema en el backend configurado; **no migra datos** de uno a otro.
- Cambiar `database_url` / `DATABASE_URL` a Postgres con una BD vacía implica empezar sin procesos ni entidades previas (el SQLite anterior sigue en disco).
- Migraciones de layout legacy (`migrate_legacy_layout`, etc.) corren **solo al arrancar la web**; el worker espera `web` healthy antes de escanear.

## Worker

- **Un solo worker** por volumen de datos: lock en `data/.worker.lock` (falla si `--scale worker=2`).
- Heartbeat en `data/worker_heartbeat.json` para el healthcheck Docker (`worker-healthcheck`).

## SEACE proxy

- El proxy `/seace/p` guarda sesiones JSF **en memoria del proceso web**. Usar **un solo worker uvicorn** (como en el Dockerfile actual) o sticky sessions; con `--workers N>1` las cookies pueden enrutar a un worker sin sesión.

## Archivos en contenedor

- `unar` + `p7zip-full` para ZIP/RAR en descargas SEACE (`document_prep.py` prueba `7z`, `unar`, `unrar`).
