# Multi-Review D: deploy / ops

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `99da74ea5a1dd6b932f475587691514184688ad2`  
**Scope:** `deploy/` plus runtime config/DB/worker/web startup context  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Qwen 3.6 Plus, GLM-5.1  
**Kimi K2.6:** timed out before returning findings  
**Excluded by request:** security / hardening review

---

## Verification note

Some reviewer findings were checked against the full repo after review. The repo **does** currently contain root `config.example.yaml`, `scripts/`, `instrucciones/`, and `apps/portal/seace_monitor/__main__.py`, so those build/entrypoint findings were treated as false positives and omitted below.

---

## Overall Assessment

The deploy surface is close, but there are a few startup and operational reliability issues worth fixing before depending on VPS deploys:

- Postgres mode has a concrete SQLAlchemy startup bug.
- The base compose is ambiguous: it starts Postgres and waits for it even when the app config defaults to SQLite.
- Web and worker both perform startup migrations/layout repair, creating race risk on shared SQLite/data volumes.
- Restart/healthcheck coverage is inconsistent between base and VPS compose files.
- Switching SQLite ↔ Postgres is schema-only; data migration is not addressed.

No security/hardening findings are included.

---

## Findings

### C1. Postgres deployments crash because `connect_args=None` is passed to SQLAlchemy
**Severity:** Critical  
**File:** `apps/portal/seace_monitor/db/session.py`  
**Flagged by:** GPT-5.5

When `DATABASE_URL` is Postgres, `connect_args` remains `{}` and `create_engine(..., connect_args=connect_args or None)` passes `None`. SQLAlchemy expects `connect_args` to be a mapping.

**Impact:** Postgres-backed web/worker can fail before DB initialization.

**Fix:** Always pass a dict or omit the argument only when empty:

```python
kwargs = {"pool_pre_ping": True}
if connect_args:
    kwargs["connect_args"] = connect_args
_engine = create_engine(database_url, **kwargs)
```

Or simply pass `connect_args=connect_args`.

---

### H1. Base compose forces a Postgres dependency even when app uses SQLite
**Severity:** High  
**File:** `deploy/docker-compose.yml`  
**Flagged by:** GLM

The base compose defines a `db` service and web/worker `depends_on: db`, but `.env.example` leaves `DATABASE_URL` commented and `config.example.vps.yaml` defaults to SQLite.

**Impact:** base compose starts and waits for an unused Postgres container, wasting resources and making the operational mode ambiguous.

**Fix:** Choose one clear model:

- make base compose SQLite-only and move Postgres into `docker-compose.postgres.yaml`; or
- make base compose Postgres-first and set/document `DATABASE_URL` accordingly.

---

### H2. Web and worker race on startup migrations/layout migration
**Severity:** High  
**Files:** `deploy/docker-compose.vps.yml`, `apps/portal/seace_monitor/web/app.py`, `apps/portal/seace_monitor/worker.py`  
**Flagged by:** GLM, DeepSeek, Qwen

Both web and worker run DB initialization and layout/data-dir migrations at startup against the same mounted data volume/SQLite DB. They can start simultaneously.

**Impact:** on upgrade with legacy layout/data, both processes can attempt filesystem moves and DB ref updates concurrently. SQLite WAL helps DB contention but does not prevent logical filesystem races.

**Fix:** Run migrations in one place only:

- a dedicated init job/container, or
- web performs migrations and worker `depends_on: web: condition: service_healthy`, or
- a shared lock/sentinel around migration functions.

---

### H3. Base compose lacks restart policies for web/worker/db
**Severity:** High  
**File:** `deploy/docker-compose.yml`  
**Flagged by:** GLM, DeepSeek, Qwen

The VPS compose uses `restart: unless-stopped`, but the base compose does not.

**Impact:** web, worker, or Postgres crashes remain down until manual intervention.

**Fix:** Add `restart: unless-stopped` to `db`, `web`, and `worker` in the base compose.

---

### H4. Base compose has no web healthcheck
**Severity:** High  
**File:** `deploy/docker-compose.yml`  
**Flagged by:** GLM, DeepSeek

The VPS compose defines a web healthcheck; the base compose only checks Postgres.

**Impact:** Docker/ops tooling cannot distinguish a healthy web app from a hung/broken one.

**Fix:** Copy the VPS web healthcheck into base compose:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://127.0.0.1:8080/"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 20s
```

---

### M1. Worker has no healthcheck in either compose
**Severity:** Medium  
**Files:** `deploy/docker-compose.yml`, `deploy/docker-compose.vps.yml`  
**Flagged by:** DeepSeek, Qwen

The worker is a long-running loop. Restart policies only handle process exit, not a hung loop or an infinite external wait.

**Impact:** scans can stop progressing while the container remains `running`.

**Fix:** Have the worker write a last-success/last-loop timestamp to a shared path, and add a healthcheck that fails when it is too old relative to `poll_interval_seconds`.

---

### M2. No guard against multiple workers with SQLite
**Severity:** Medium  
**File:** `deploy/docker-compose.vps.yml`  
**Flagged by:** GLM

An operator can accidentally run `--scale worker=2` while both workers share the same SQLite DB and data volume.

**Impact:** concurrent scans/downloads can contend on SQLite and duplicate external work.

**Fix:** Add an application-level lock file/advisory lock for worker loop ownership, and document/encode single-worker assumption. If using Compose deploy metadata, indicate `replicas: 1` where applicable.

---

### M3. SQLite-to-Postgres migration is not documented or implemented
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/db/session.py`, `deploy/.env.example`  
**Flagged by:** GLM

`init_db()` creates schema on the selected backend, but there is no data migration path. Switching from SQLite to Postgres starts with an empty Postgres DB.

**Impact:** operators may think setting `DATABASE_URL` migrates data; processes/entities/analyses remain in the old SQLite file.

**Fix:** Document that backends are independent, and provide/export a migration script if Postgres is a supported upgrade path.

---

### M4. README / compose env-file behavior is easy to misapply
**Severity:** Medium  
**File:** `README.md`, `deploy/docker-compose.yml`  
**Flagged by:** GPT-5.5

The README flow instructs copying `deploy/.env.example` to `deploy/.env`, then running compose from repo root with `-f deploy/docker-compose.yml`. Depending on Compose project-directory/env-file behavior, operators may expect `deploy/.env` to be loaded when it is not.

**Impact:** `GEMINI_API_KEY`, proxy, or project-name variables can resolve empty/default despite the user filling `deploy/.env`.

**Fix:** Make the command explicit:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

or document `cd deploy && docker compose ...`, with paths adjusted accordingly.

---

### M5. RAR5 support may be missing in the image
**Severity:** Medium  
**File:** `deploy/Dockerfile`  
**Flagged by:** GLM

The Dockerfile installs `p7zip-full`. Depending on distro/tool support, RAR5 archives may fail while SEACE downloads can include modern RAR files.

**Impact:** archive extraction can fail at runtime for valid tender packages.

**Fix:** Add a RAR5-capable tool such as `unar`, `unrar-free`, or `libarchive-tools`, and ensure `document_prep.py` tries installed tools deterministically.

---

### L1. Base compose lacks explicit project name
**Severity:** Low  
**File:** `deploy/docker-compose.yml`  
**Flagged by:** GLM

The VPS compose sets `name: ${COMPOSE_PROJECT_NAME:-tender-workflows}`; base compose does not.

**Impact:** container/network/volume names vary depending on working directory and compose invocation.

**Fix:** Add the same `name:` line to base compose.

---

## Recommended Fix Order

1. **C1** — fix SQLAlchemy `connect_args` before any Postgres deploy testing.
2. **H1** — clarify base compose: SQLite-only or Postgres-first.
3. **H2** — make startup migrations single-owner/locked.
4. **H3/H4/M1** — add restart policies and healthchecks consistently.
5. **M2** — enforce/document single-worker with SQLite.
6. **M3/M4** — document backend migration and env-file commands.
7. **M5/L1** — dependency/project-name polish.

## Notes

No security/hardening review was performed. Findings focus on deploy correctness, startup reliability, persistence, runtime dependencies, and operational recoverability.
