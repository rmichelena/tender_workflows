# Contrato de ingesta, feed/pipeline y multi-tenant

Documento canónico de la **arquitectura objetivo** para que las fuentes de entrada
funcionen como plugins, el ruido del descubrimiento se separe del trabajo curado, y
todo escale a multi-tenant sin repetir scanners.

**Estado:** diseño objetivo — rama de trabajo `ingest-plugin-contract`. El código en
`main` (incl. ADP) todavía **no** implementa este contrato; ver [§1](#1-problema-actual).
**Relacionado:** [ARCHITECTURE.md](ARCHITECTURE.md), [INPUT_SOURCES.md](INPUT_SOURCES.md),
[MULTI_TENANCY.md](MULTI_TENANCY.md), [STAGES.md](STAGES.md), [ROADMAP.md](ROADMAP.md).

---

## Resumen en tres pilares

1. **Contrato `SourceAdapter`** — cada fuente (SEACE, ADP, email, …) es un plugin que
   implementa una interfaz de comportamiento. El núcleo orquesta; el adapter solo
   aporta lo específico del portal (cliente HTTP + parser + URL + perfil de lectura).
2. **Split Feed / Pipeline** — el descubrimiento ruidoso (99% no interesa) vive en un
   **feed** barato y purgable; el trabajo curado (descargado/analizado/oportunidad)
   vive en un **pipeline** que se crea solo ante una acción positiva, copiando un
   snapshot del feed **sin foreign key**.
3. **Multi-tenant por capas** — el **feed es compartido** (un solo scan sirve a todos),
   las **reglas/decisiones son un overlay por tenant**, y el **pipeline es privado**
   por tenant. Lo compartido nunca lleva `tenant_id`; lo privado siempre.

Los tres se refuerzan: el adapter escribe en el feed compartido y nunca conoce tenants;
el núcleo nunca conoce el HTML de un portal.

---

## 1. Problema actual

El código hoy tiene un contrato de plugin **nominal**, no funcional:

- `ingest/base.py` define `IngestAdapter` solo con `source`, `label`, `capabilities`.
  **No tiene métodos de comportamiento.** `get_adapter()`/`capabilities` solo se usan en
  tests; ningún flujo de producción los consulta.
- El comportamiento real se enrama con `if process.source == "adp_portal"` en
  `worker.py`, `analysis/runner.py` y `web/seace_view.py`, y se duplican archivos
  `adp_scanner.py` / `adp_watchlist.py` / `adp_downloader.py` que reimplementan lo que
  SEACE ya resolvió (upsert, auto-reject, savepoints, máquina de watchlist, manifest).
- `Process` está **sobrecargada**: es a la vez la fila del feed ruidoso, el item de
  trabajo y el contenedor del análisis. De ahí la tabla "ancha" con campos SEACE-only
  (`nid_proceso`, `link_id`, …) que otras fuentes dejan NULL.
- El autoreject **muta** el item (`status=autorejected` + `auto_reject_exempt` en la
  misma fila), lo que es incompatible con un feed compartido entre tenants.

Consecuencia: agregar una fuente es *cirugía a escopetazo*; agregar un tenant requeriría
repetir scanners o aislar mal los datos.

---

## 2. Pilar 1 — Contrato `SourceAdapter`

El adapter expone **comportamiento**, no solo metadatos. El núcleo lo invoca a través de
la interfaz; nadie ramifica por `source`.

```python
class SourceAdapter(Protocol):
    source: str                      # "seace", "adp_portal", "email", …
    label: str                       # "SEACE", "ADP Portal", …
    capabilities: IngestCapabilities

    # Descubrimiento → escribe en el FEED compartido (no en pipeline, no en tenant).
    def discover(self, ctx: ScanContext) -> Iterable[DiscoveredItem]: ...

    # Re-fetch de un item ya conocido para detectar cambios (watchlist).
    def detect_changes(self, feed_item: FeedItem) -> ChangeSet: ...

    # Índice de documentos del item (sin descargar bytes).
    def fetch_document_index(self, ref: SourceRef) -> list[DocumentRef]: ...

    # Descarga de bytes al directorio destino.
    def download_documents(self, refs: list[DocumentRef], dest_dir: Path) -> int: ...

    # URL/acción "Ver en el portal" para la UI (None si no aplica).
    def external_url(self, ref: SourceRef) -> str | None: ...

    # Perfil de free reader sugerido por la fuente (el núcleo puede sobreescribir).
    def reader_profile(self, item: DiscoveredItem) -> str: ...
```

### DTOs normalizados (frontera con el ORM)

El adapter devuelve estructuras **normalizadas**; el núcleo las mapea a tablas. Así el
núcleo nunca interpreta `documentos_json` con forma de portal.

| DTO | Rol |
|-----|-----|
| `DiscoveredItem` | Item descubierto: `source`, `source_ref`, `dedup_key`, `entity_ref`, `objeto`, `descripcion`, `publicado_en`, `raw_payload`, `documents: list[DocumentRef]` |
| `DocumentRef` | Documento: `external_id`, `title`, `download_hint`, `dates`, `bytes_hint` (sin acoplar a `uuid`/`tipo_descarga` de SEACE ni `name_file` de ADP) |
| `ChangeSet` | Diff de un re-fetch: cronograma cambiado, documentos nuevos/eliminados, `content_hash` |
| `ScanContext` | Entrada del scan: qué entidades escanear (unión de tenants), proxy, límites |
| `SourceRef` | Referencia estable a un item dentro de su fuente |

### Reparto de responsabilidades

| Vive en el **adapter** (específico del portal) | Vive en el **núcleo** (común a todas las fuentes) |
|---|---|
| Cliente HTTP / sesión / paginación | Upsert al feed + `content_hash` + dedupe |
| Parser HTML/JSON → `DiscoveredItem` | Savepoints por item, commit por entidad |
| Reglas de URL del portal | Máquina de estados de watchlist |
| Descarga concreta (Alfresco / GET directo) | Manifest, `extract_archives`, normalización de nombres |
| Perfil de lectura sugerido | Auto-reject (overlay por tenant), promoción, free reader |

El registry deja de ser decorativo: el worker hace `for src in registered_sources()` y
respeta `capabilities` (`scan_listings`, `fetch_by_reference`, `create_from_upload`) en
vez de hardcodear SEACE + `if cfg.adp.enabled`. SEACE debe ser el **primer ciudadano**
del contrato, no una excepción.

---

## 3. Pilar 2 — Split Feed / Pipeline

### FeedItem (contexto Descubrimiento)

Alto churn, barato, **purgable**. Es el `IngestEvent` materializado.

- `id`, `source`, `source_ref`, `dedup_key`, `content_hash`
- `entity_ref`, `objeto`, `descripcion`, `raw_payload_json`
- `first_seen_at`, `last_seen_at`
- `status_feed`: `new | seen | promoted | expired`
- **Sin `tenant_id`** (es compartido, ver [§4](#4-pilar-3--multi-tenant-por-capas)).
- **Vista mailbox por fuente:** `WHERE source = ?` (más búsqueda unificada todas-las-fuentes).
- **Autopurge:** `DELETE WHERE status_feed != 'promoted' AND last_seen_at < now-90d`
  (parametrizable; los `promoted` nunca se purgan).

### PipelineItem (contexto Trabajo)

Bajo volumen, vive para siempre, **privado por tenant**.

- `id` — **UUID interno inmutable**. Nunca se deriva del canal, nunca cambia.
- `origin_source`, `origin_source_ref`, `origin_feed_id` — **snapshot, SIN foreign key**
  (el feed puede purgarse sin romper esto).
- Campos curados copiados en la promoción (`objeto`, `descripcion`, …).
- `workflow_profile`, `interest_status`, `lifecycle_phase`, `status` (operativo del portal).
- Relaciones: `AnalysisResult`, documentos en disco, `ExternalRef[]`.

### Identidad estable y multi-canal (`ExternalRef`)

La identidad de negocio **no se ata al canal**. Un `PipelineItem` puede tener varias
referencias externas a lo largo de su vida:

```text
PipelineItem (id=uuid)
  └─ ExternalRef(source="email",  source_ref="<hash msgid+asunto+fecha>")
  └─ ExternalRef(source="seace",  source_ref="<nid_proceso>")
```

Caso de uso resuelto: algo que **llega por correo** y luego **se publica como licitación**
es el **mismo item**. No se cambia el UID; se agrega un `ExternalRef` nuevo y se avanza la
fase. El "UID autogenerado" de un correo es un `source_ref` sintético dentro de
`source=email`, **no** la identidad del negocio.

> El `UniqueConstraint(source, entity_id, source_ref)` actual de `Process` impide esto
> hoy (forzaría dos filas). El target lo mueve a `ExternalRef` con identidad interna propia.

### `lifecycle_phase` — eje ortogonal

No mezclar tres cosas que hoy se solapan:

| Eje | Qué es | Ejemplos |
|-----|--------|----------|
| `status` | Estado operativo del portal | `publicada … portafolio` |
| `stage` (A–D) | Etapa de **procesamiento documental** | fast reader, conversión, agente |
| `lifecycle_phase` | **Fase comercial** del objeto | `pre_licitacion`/`estudio_mercado` → `licitacion` → `adjudicacion` → `ejecucion` |

El **estudio de mercado no es un tipo de proceso separado**: es una `lifecycle_phase`
temprana del mismo item, que puede transicionar a `licitacion` sin duplicarse.

> **Implementado (aditivo, pre-split):** `LifecyclePhase`
> (`estudio_mercado`/`licitacion`/`adjudicacion`/`ejecucion`) y la columna
> `Process.lifecycle_phase` (default `licitacion`, índice + backfill, mismo patrón
> que `workflow_profile`/`interest_status`). Vive en `Process` hasta el split, donde
> migra a `PipelineItem`. Aún no se expone en UI ni hay transiciones automáticas.

### Promoción (feed → pipeline)

Una **acción positiva** (descargar / analizar / marcar watchlist / marcar interés):

1. Copia el snapshot necesario del `FeedItem` al `PipelineItem` (del tenant que actúa).
2. Marca `FeedItem.status_feed = promoted` (lo excluye de la purga y lo muestra como "en
   seguimiento" en el inbox).
3. Guarda `origin_*` como columnas planas (sin FK).

### Watchlist = refresh del feed que propaga al pipeline

El "watch for changes" es naturalmente una operación del **feed** (re-scan del portal vía
`adapter.detect_changes`). Al detectar cambios, actualiza el `FeedItem` por `dedup_key` y
**propaga** el `ChangeSet` a los `PipelineItem` promovidos que lo referencian. Hoy esto
está fundido en `Process`; separarlo limpia responsabilidades y deduplica entre tenants.

---

## 4. Pilar 3 — Multi-tenant por capas

Requisito de producto: **no repetir scanners** (son el valor y el costo), pero cada tenant
tiene **sus reglas de autoreject** y **su set propio** de descargadas/analizadas/oportunidades.

```text
┌─────────────────────────────────────────────────────────┐
│  FEED  (global, compartido, SIN tenant_id)               │
│  scan SEACE/ADP/… una sola vez → firehose público        │
│  autopurge >90d · detect_changes (watchlist refresh)     │
└───────────────┬─────────────────────────────────────────┘
                │  cada tenant lo ve filtrado por SUS reglas
        ┌───────┴───────┐        ┌───────────────┐
        ▼               ▼        ▼               ▼
┌──────────────┐   ┌──────────────┐    OVERLAY por tenant:
│ OVERLAY t-A  │   │ OVERLAY t-B  │    - reglas autoreject
│              │   │              │    - exempt / visto / oculto
└──────┬───────┘   └──────┬───────┘    - selección de entidades
       │ promoción        │ promoción
       ▼                  ▼
┌──────────────┐   ┌──────────────┐    PIPELINE privado por tenant:
│ PIPELINE t-A │   │ PIPELINE t-B │    PipelineItem (UUID estable),
│ descargadas… │   │ descargadas… │    AnalysisResult, oportunidades,
│ oportunidades│   │ oportunidades│    ExternalRef[], lifecycle_phase
└──────────────┘   └──────────────┘
```

### Reglas de capa

- **El feed es compartido y agnóstico al tenant.** Se escanea SEACE/ADP **una vez** para
  todos. Escanear por tenant sería N× costo y N× carga sobre el portal, con datos públicos
  e idénticos.
- **El autoreject es un overlay por tenant, NO una mutación del feed.** El scanner del feed
  **ya no ejecuta autoreject**; solo registra el item crudo. `autorejected`, `auto_reject_exempt`,
  "visto/oculto" pasan a una tabla `(tenant_id, feed_item_id, decision)` —o se computan al
  vuelo al render del inbox y se materializan solo ante una acción explícita (exempt/restaurar).
- **La selección de entidades es por tenant; el scan cubre la unión.** El worker escanea la
  unión de entidades que interesan a *cualquier* tenant; cada tenant ve solo las suyas
  (otro filtro del overlay).
- **El pipeline es privado.** `PipelineItem` y `AnalysisResult` siempre llevan `tenant_id`;
  aislamiento estricto. El feed (datos públicos) no es sensible; el pipeline (decisiones y
  análisis comerciales) sí.

### Invariante

> **Lo compartido (feed, scanners, catálogo OSCE) nunca lleva `tenant_id`.
> Lo privado (overlay de reglas + pipeline + análisis) siempre lo lleva.
> El límite feed↔pipeline es snapshot, no relacional.**

### Partición física de DB (cuándo)

- **Ahora / refactor:** una sola DB; separación **lógica**: feed (sin `tenant_id`) y
  pipeline (con `tenant_id`). Tenant implícito `default`. Sacar las decisiones de tenant
  fuera del feed item.
- **Fase 4 (multiusuario):** opcionalmente mover el **pipeline a una DB por tenant**
  (encaja con el layout `data/tenants/{id}/`: aislamiento, backup y borrado por tenant).
  Como el copy feed→pipeline es sin FK, cruzar el límite de DB es trivial.
- **No** hacer "una DB por source": los sources son una columna del feed compartido; una
  DB por source rompería la búsqueda unificada y multiplicaría archivos por tenant.

---

## 5. Modelo de datos objetivo

| Tabla | Tenant | Campos clave |
|-------|--------|--------------|
| `FeedItem` | **compartido** | `source`, `source_ref`, `dedup_key`, `content_hash`, `objeto`, `descripcion`, `raw_payload_json`, `first_seen_at`, `last_seen_at`, `status_feed` |
| `TenantFeedDecision` | por tenant | `(tenant_id, feed_item_id)`, `decision` (`autorejected`/`exempt`/`hidden`/`seen`), `rule_id`, `reason` |
| `Entity` | compartido (catálogo) + selección por tenant | catálogo OSCE; `activa` pasa a selección por tenant |
| `PipelineItem` | por tenant | `id` (UUID), `tenant_id`, `origin_source`, `origin_source_ref`, `origin_feed_id` (sin FK), `workflow_profile`, `interest_status`, `lifecycle_phase`, `status`, campos curados |
| `ExternalRef` | por tenant (con el item) | `pipeline_item_id`, `source`, `source_ref`, `trigger`, `first_seen_at` |
| `AnalysisResult` | por tenant | igual que hoy, colgando de `PipelineItem` |
| `DocumentPackage` (futuro) | por tenant | paquetes documentales versionados (ver [INPUT_SOURCES.md](INPUT_SOURCES.md)) |

`Process` actual = se descompone en `FeedItem` + `PipelineItem` (+ overlay). El renombre
es la deuda mayor; ver plan incremental abajo.

---

## 6. Flujos con el modelo objetivo

| Flujo | Quién | Qué hace |
|-------|-------|----------|
| **Scan** | worker → `adapter.discover()` | upsert al `FeedItem` compartido por `dedup_key`; sin autoreject, sin tenant |
| **Inbox de un tenant** | núcleo | feed filtrado por reglas + selección de entidades del tenant; `autorejected` computado/overlay |
| **Promoción** | acción UI del tenant | snapshot `FeedItem` → `PipelineItem(tenant_id)`; `status_feed=promoted` |
| **Download** | `adapter.download_documents()` | bytes a `tenants/{id}/procesos/...`; núcleo hace manifest/extract |
| **Analyze** | núcleo + free reader | perfil por `entity/source/workflow_profile/stage`; resultado en `AnalysisResult` del tenant |
| **Watchlist** | worker → `adapter.detect_changes()` | refresca `FeedItem`; propaga `ChangeSet` a `PipelineItem` promovidos |
| **Ver en portal** | UI → `adapter.external_url()` | sin `if source ==` en la vista |

---

## 7. Plan de refactor incremental (sin romper producción)

Orden pensado para que cada paso sea mergeable y deje el sistema funcionando con tenant
`default`:

1. **Contrato:** definir `SourceAdapter` + DTOs; reescribir **SEACE** sobre el contrato
   (sin cambiar comportamiento). Worker/runner/UI consumen `get_adapter()` y `capabilities`.
2. **ADP al contrato:** colapsar `adp_scanner`/`adp_watchlist`/`adp_downloader` a un
   `AdpAdapter` (client+parser+url). Eliminar el branching por string.
3. **Feed/Pipeline (lógico):** introducir `FeedItem` y `PipelineItem` como contextos; en
   esta fase pueden convivir como vistas/tablas derivadas de `Process` para migrar datos.
   Sacar autoreject del scanner a overlay.
4. **Identidad:** `ExternalRef` + UUID interno; relajar el `UniqueConstraint` atado a source.
5. **Lifecycle:** `lifecycle_phase`; `market_study` como fase, no como tipo separado.
6. **Multi-tenant (lógico):** `tenant_id` en pipeline/overlay; feed sin `tenant_id`;
   selección de entidades y reglas por tenant. Tenant `default`.
7. **Multi-tenant (físico, Fase 4):** opcional, pipeline en DB por tenant.

Cada paso actualiza los docs relacionados en el mismo PR.

---

## 8. Decisiones abiertas

- **`dedup_key` por fuente — decidido:** lo encapsula `adapter` (SEACE = nomenclatura
  normalizada, ya es el UID de facto al mergear re-publicaciones; ADP = clave de su
  scanner; email = hash de msgid+asunto+fecha).
- **Docs públicos entre tenants — decidido (inicial):** copia por tenant (aislamiento
  simple); content-addressed store queda como optimización futura.
- **Overlay de autoreject materializar vs al vuelo — decidido:** **materializar**
  (`TenantFeedDecision`), por paridad con el comportamiento actual (status persistido) y
  rendimiento del inbox; computar al vuelo queda como optimización futura.
- **Rename `Process` — decidido:** **no** renombrar la tabla física en el paso 3; se
  introducen seams sobre `processes` y el split físico es el último sub-paso (gated).
- Formato de `manifest.json` por paquete documental y cuándo formalizar `DocumentPackage`
  (sigue abierto).

---

## 9. Plan detallado del paso 3 — split Feed/Pipeline (roadmap 0.3)

Cada sub-paso es un PR/commit independiente, deploy-safe y deja el sistema funcionando
con tenant `default`. Se valida con suite verde local (ignorando la falla pre-existente
`test_migrate_process_identity_schema_relaxes_legacy_sqlite_nid`) y, en los sub-pasos con
riesgo de comportamiento, comparando conteos por estado/lista en el VPS antes/después.

| Sub-paso | Qué | Riesgo | Notas |
|----------|-----|--------|-------|
| **0.3a** | **Seam feed (sin cambio físico):** módulo `feed/repository.py` con API conceptual `FeedItem` (`upsert_discovered`, `mark_seen/promoted`, `query_inbox(source, …)`) operando sobre `processes`. Scanner y list views pasan a usarlo. | Bajo (behavior-preserving) | No toca la BD. Tests de paridad. **Punto de arranque.** |
| **0.3b** | **Overlay `TenantFeedDecision` (aditivo):** tabla `(tenant_id, feed_item_id, decision, rule_id, reason, created_at)` con `tenant_id='default'`. Backfill desde `status=autorejected`→`autorejected` y `auto_reject_exempt=True`→`exempt`. Doble escritura transitoria. | Bajo (solo CREATE TABLE) | Backup antes. Reversible. |
| **0.3c** | **Mover autoreject del scanner al overlay:** `apply_auto_reject_rules` deja de mutar `process.status`; escribe `TenantFeedDecision`. Scanner solo registra item crudo. Listas de descartados/exempt leen del overlay. | **Medio** (comportamiento) | Tests exhaustivos + verificación de conteos en VPS. |
| **0.3d** | **Promoción explícita feed→pipeline (lógica):** acción positiva (descargar/analizar/watchlist/interés) marca `feed.status='promoted'` y consolida snapshot curado. Con `Process` aún única, "promoción" = flags/columnas. | Bajo–medio | Prepara la copia sin FK del split físico. |
| **0.3e** | **Split físico (gated, opcional):** solo si 0.3a–d estables en VPS. Crear `pipeline_items` (migrar `promoted`), `processes`→`feed_items`. Copia sin FK. | **Alto** (destructivo) | Backup + ventana. Único paso irreversible. |

**Decisiones resueltas que aplican aquí:** ver §8 (dedup_key vía adapter, overlay
materializado, sin rename físico hasta 0.3e, copia de docs por tenant).
