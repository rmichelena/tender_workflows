# Multi-tenancy — principios de diseño

Decisiones transversales para que el portal, los datos en disco y la capa agentica escalen a **varios usuarios sin un stack Docker por persona**.

**Relacionado:** [INGEST_CONTRACT.md](INGEST_CONTRACT.md) (modelo feed/pipeline + capas), [ARCHITECTURE.md](ARCHITECTURE.md), [HERMES_VPS.md](HERMES_VPS.md), [ROADMAP.md](ROADMAP.md) Fase 4.

---

## Principio rector: lo compartido no lleva `tenant_id`, lo privado sí

El multi-tenant se modela en **tres capas** (detalle en [INGEST_CONTRACT.md §4](INGEST_CONTRACT.md#4-pilar-3--multi-tenant-por-capas)):

| Capa | Tenant | Qué incluye |
|------|--------|-------------|
| **Feed** (descubrimiento) | **compartido** | Resultado de los scanners; **se escanea una sola vez** para todos. Datos públicos (licitaciones). **Sin `tenant_id`**. |
| **Overlay** (decisiones) | por tenant | Reglas de autoreject, exempt/visto/oculto, **selección de entidades**. |
| **Pipeline** (trabajo) | por tenant | Descargadas, analizadas, oportunidades, análisis. Aislamiento estricto. **Siempre `tenant_id`**. |

**Implicaciones que corrigen supuestos previos:**

- **No repetir scanners por tenant.** El scan es el valor y el costo (proxies, sesiones JSF, rate-limits). Un solo scan alimenta el feed compartido.
- **El autoreject es un overlay por tenant, NO se ejecuta en el scanner.** El scanner registra el item crudo en el feed; cada tenant lo filtra con SUS reglas. `autorejected`/`auto_reject_exempt` dejan de ser estado global del item.
- **La selección de entidades es por tenant; el worker escanea la unión.** Cada tenant ve solo las entidades que eligió (filtro del overlay).

---

## Principios

1. **Un solo despliegue** de portal + worker (+ opcionalmente un gateway de mensajería). No `docker compose` por usuario.
2. **Partición en disco por tenant** (usuario u organización): settings, cookies SEACE, expedientes, sesiones agente.
3. **Dropbox fuera** del camino crítico (sync lento, permisos, no multi-tenant). Datos en volumen host o object storage.
4. **BD es fuente de verdad** para ownership, permisos y estado; el filesystem refleja `tenant_id` + `process_id`. El **feed compartido** es la excepción: no se particiona por tenant.
5. **Hermes es un adapter opcional** de canal/agente, no el dueño del modelo de usuarios ni de los expedientes.
6. **Migración suave:** hoy un tenant implícito `default`; mañana `users` + `organizations` sin mover el monorepo.

---

## Modelo de identidad (objetivo)

```mermaid
erDiagram
  Organization ||--o{ Membership : has
  User ||--o{ Membership : has
  Organization ||--o{ PipelineItem : owns
  User ||--o{ PipelineItem : created_by
  Organization ||--o{ TenantFeedDecision : "filtra feed con"
  FeedItem ||--o{ TenantFeedDecision : "es decidido por"
  FeedItem ||--o{ PipelineItem : "promovido a (snapshot, sin FK)"
  FeedItem {
    int id
    string source
    string source_ref
    string status_feed
  }
  PipelineItem {
    uuid id
    string tenant_id
    string origin_source
    string origin_source_ref
    string lifecycle_phase
    string status
  }
```

| Concepto | Rol |
|----------|-----|
| **User** | Persona que inicia sesión en el portal |
| **Organization** | Equipo / empresa (opcional al inicio; un user = una org) |
| **Tenant** | Clave de partición en disco y en queries (`tenant_id` = org id o user id) |
| **FeedItem** | Item descubierto por un scanner. **Compartido, sin `tenant_id`** |
| **PipelineItem** | Lo que un tenant decidió trabajar; **siempre scoped a `tenant_id`** (reemplaza a `Process` como objeto privado) |

Reglas de acceso: un usuario solo ve **su pipeline** y el feed filtrado por sus reglas; admin de org gestiona la **selección de entidades** y las **reglas de prefiltro** de su tenant. El feed crudo y el catálogo OSCE son compartidos.

---

## Layout en disco (canónico)

Volumen host compartido entre **portal**, **worker** y (si aplica) **un** contenedor Hermes:

```
/data/
  platform/                    # read-only: instrucciones/, scripts/ (montaje del repo)
  tenants/
    {tenant_id}/
      settings/
        portal.yaml            # providers LLM, modelos, poll_interval, …
        filter_rules.yaml      # prefiltros descarte / auto-análisis
      secrets/                 # gitignored; API keys si no van solo en env
      seace/
        cookies/               # sesión JSF por tenant (si no global)
        cache/
      procesos/
        {nid}_{nomenclatura}/  # igual que hoy: documentos/, free_reader_summary.md, …
      agent/                   # home Hermes u orquestador para este tenant
        sessions/
        memories/
        config.yaml            # overlay modelo/tools solo de este tenant
  _system/
    migrations/
    audit.log
```

**Hoy (pre-multiuser):** un solo tenant `default`:

```
/data/tenants/default/procesos/...
```

El código actual usa `data/procesos/` → migración: `data_dir = {base}/tenants/{tenant_id}/procesos` con `tenant_id=default`.

---

## Qué va dónde

| Dato | Ubicación | Notas |
|------|-----------|-------|
| Settings LLM (GenAI / OpenRouter / Fireworks) | `tenants/{id}/settings/portal.yaml` + UI | Fase 2 roadmap |
| Reglas prefiltro (autoreject) | `filter_rules.yaml` + overlay BD por tenant | **Evaluadas por tenant sobre el feed**, NO en el scanner |
| Feed crudo (scanners) | Tabla `FeedItem` global | **Compartido**, sin `tenant_id`, autopurge >90d |
| Catálogo de entidades | Tabla `Entity` (catálogo OSCE) | Compartido; la **selección `activa` es por tenant** |
| Documentos descargados | `tenants/{id}/procesos/.../documentos/` | Sin Dropbox; parte del pipeline privado |
| Cookies SEACE | `tenants/{id}/seace/` | Si varios usuarios abren SEACE distinto |
| Sesión chat agente | BD + `tenants/{id}/agent/sessions/` | Portal es dueño del `session_id` |
| SQLite/Postgres | feed global + pipeline por tenant | Empezar todo en una DB (feed sin `tenant_id`, pipeline con `tenant_id`); Fase 4 puede mover el pipeline a DB por tenant |

---

## Hermes en un mundo multiusuario

### Qué es «profile» en Hermes hoy (v0.14 en VPS)

- `hermes profile` = **instalación/distribución** (modelo default, alias CLI, gateway asociado).
- En el VPS hay **un profile `default`** y contenedores extra (`hermes-debs`, `hermes-9`) = **otro HERMES_HOME por contenedor**, no multi-tenant dentro de un proceso.

Eso **no escala** al modelo que queremos (evitar N contenedores).

### Multi-agent en Hermes (PR #25660, aún no en v0.14)

Diseño upstream: **un gateway**, N agentes con `home_dir` separado y routing por metadata (Telegram user, chat, etc.).

| Escenario upstream | ¿Nos sirve? |
|--------------------|-------------|
| 1 gateway, N agentes, `home_dir` por agente | **Parcial** — si mapeamos `tenant_id` → agente |
| Routing por Telegram/Discord | Canales externos (Fase 5 WhatsApp) |
| Routing por sesión web del portal | **No documentado aún** — habría que enrutar por `agent_id` explícito al invocar |

**Conclusión:** Hermes puede seguir siendo el **motor de agente** (tools, skills, gateway WhatsApp) con **un contenedor** y N `home_dir` bajo `/data/tenants/{id}/agent/`, **cuando** exista multi-agent estable. No usar «un contenedor Hermes por usuario».

### Si Hermes no encaja en web multi-tenant

Plan B (compatible con el mismo layout de disco):

- Portal + **@cursor/sdk** u orquestador propio leyendo `instrucciones/`.
- LLM vía abstracción provider (GenAI / OpenRouter / Fireworks).
- Hermes solo para **Telegram/WhatsApp** como canal satélite, leyendo paths bajo `tenants/{id}/procesos/`.

La decisión se pospone hasta Fase 4; el **filesystem por tenant** sirve para ambos caminos.

---

## Implicaciones para decisiones **ahora**

| Decisión | Hacer | Evitar |
|----------|-------|--------|
| Volumen compartido VPS | Bind `/data` con árbol `tenants/default/` | Volumen Docker anónimo sin path host |
| Dropbox / hermes-shared | No usar para SEACE | Symlinks a Dropbox en flujo automático |
| Contenedores | 1× web, 1× worker, 0–1× Hermes gateway | 1× Hermes por usuario |
| Paths en código | `tenant_data_dir(tenant_id)` helper | Hardcode `data/procesos/` |
| BD | `tenant_id` en pipeline/overlay → `default`; feed **sin** `tenant_id` | `tenant_id` en el feed; autoreject que mute el item compartido |
| Integración agente | Pasar `tenant_id` + path absoluto al continuar | Copiar PDFs a otro árbol manual |
| Permisos Unix | Grupo compartido o ACL por `tenants/{id}/` | root-only sin plan para uid 10000 |

---

## Fases alineadas

| Fase | Multi-tenant |
|------|----------------|
| **Ahora** | Tenant implícito `default`; documentar layout `tenants/` |
| **Volumen Hermes** | Montar `/data` en portal + Hermes; paths bajo `tenants/default/` |
| **Settings LLM** | Archivo por tenant desde el inicio |
| **Prefiltros** | Reglas por tenant, evaluadas como overlay sobre el feed compartido |
| **Fase 4** | Auth, `users`, `organizations`, `tenant_id` en pipeline/overlay (no en feed); opcional pipeline en DB por tenant |
| **Agente** | `home_dir = tenants/{id}/agent` o SDK con mismo path |

---

## Migración desde el estado actual

1. Crear `/data/tenants/default/procesos/` y mover contenido de `data/procesos/`.
2. Introducir `AppConfig.tenant_id = "default"` y helper de paths.
3. Separar `Process` en `FeedItem` (sin `tenant_id`) + `PipelineItem` (con `tenant_id`, default `'default'`) + overlay de decisiones por tenant; sacar el autoreject del scanner. Ver [INGEST_CONTRACT.md §7](INGEST_CONTRACT.md#7-plan-de-refactor-incremental-sin-romper-producción).
4. Recrear Hermes con mount `/data:/data:rw` y `HERMES_HOME` o agent `home_dir` apuntando a subcarpeta (no todo `.hermes` mezclado con tenders).
