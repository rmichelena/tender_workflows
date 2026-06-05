# Arquitectura — tender_workflows

Documento de referencia del sistema integrado: monitoreo SEACE, portal, pipeline documental y fase agentica.

**Estado:** refleja el código en `main` a junio 2026 (incluye fuente SEACE + ADP).  
**Relacionado:** [INGEST_CONTRACT.md](INGEST_CONTRACT.md) (arquitectura objetivo de plugins + feed/pipeline + multi-tenant), [STAGES.md](STAGES.md) (modelo canónico A→D), [INPUT_SOURCES.md](INPUT_SOURCES.md), [ROADMAP.md](ROADMAP.md), [INTEGRATION.md](INTEGRATION.md).

> **Nota de arquitectura objetivo:** la multi-ingesta hoy funciona pero acopla el
> comportamiento por `source` (branching + archivos `adp_*`/`seace_*` paralelos). El
> diseño objetivo (contrato `SourceAdapter`, split feed/pipeline e identidad estable) está
> en [INGEST_CONTRACT.md](INGEST_CONTRACT.md) y se ejecuta en la rama `ingest-plugin-contract`.

---

## Visión en una frase

Un monorepo que **ingiere items del pipeline** por múltiples canales y entrypoints (SEACE hoy; portales privados, email y manual planificados), permite **decisión humana** en el portal (interés, análisis, portafolio), conecta con **conversión documental** (etapa C) y habilita **trabajo agentico en portafolio** (etapa D). Ver [STAGES.md](STAGES.md) e [INPUT_SOURCES.md](INPUT_SOURCES.md).

---

## Capas ortogonales

Tres dimensiones que no deben mezclarse en el modelo de datos:

| Dimensión | Qué es | Hoy | Futuro |
|-----------|--------|-----|--------|
| **Canal de ingesta (`source`)** | De dónde llega el item | Adapter SEACE (`seace_monitor`) | Portales privados, email, manual |
| **Trigger** | Qué provocó revisar la fuente | Worker periódico SEACE | Change Detection webhook, mailbox poll, alta manual |
| **Perfil de workflow** | Qué ruta ejecutar | `public_tender` implícito | `private_tender`, `market_study`, `multilateral`, … |
| **Estado de interés** | Qué tan comercialmente interesante es | Implícito en acciones UI | `none`, `watching`, `candidate`, `opportunity`, `rejected` |
| **Contexto de negocio** | Qué hacemos después del go/no-go | Portafolio manual | Catálogo, propuesta, flujo de caja |

```mermaid
flowchart TB
  subgraph ingest [Capa ingesta — adapters]
    SEACE[Adapter SEACE]
    PORTAL[Portales cliente — planificado]
    EMAIL[Adapter email — planificado]
    MANUAL[Upload manual — planificado]
    CD[Change Detection — trigger]
    WA[WhatsApp notificaciones — planificado]
  end

  subgraph core [Capa dominio — pipeline items]
    DB[(SQLite / Postgres)]
    PROC[Process hoy / PipelineItem conceptual]
    STATE[Estados del workflow]
    INTEREST[interest_status]
    RULES[Motor de reglas — planificado]
  end

  subgraph process [Capa procesamiento]
    DL[Descarga Alfresco]
    FAST[Fast-path Gemini multi-PDF]
    TP[Paso 1.0–1.3 determinístico]
    AG[Agentes 1.5+ vía Hermes]
  end

  subgraph ui [Presentación]
    WEB[Portal FastAPI]
    CHAT[Chat embebido — planificado]
  end

  CD -.-> PORTAL
  SEACE --> PROC
  PORTAL -.-> PROC
  EMAIL -.-> PROC
  MANUAL -.-> PROC
  PROC --> DB
  PROC --> INTEREST
  RULES -.-> PROC
  WEB --> PROC
  WEB --> DL --> FAST
  DL --> TP
  FAST --> STATE
  TP --> STATE
  STATE -->|portafolio| AG
  CHAT -.-> AG
  WA -.-> WEB
```

---

## Bounded contexts (largo plazo)

| Contexto | Responsabilidad | En repo hoy |
|----------|-----------------|-------------|
| **Intelligence** | Ingesta, estados, expedientes, reglas | `apps/portal/seace_monitor/` |
| **Analysis** | Paso 1, fast-path, agentes, BOM | `instrucciones/`, `scripts/`, `analysis/` |
| **Catalog** | SKUs, specs, costos | No implementado |
| **Proposal** | Redacción, pricing | No implementado |
| **Finance** | Flujo de caja vs hitos licitación | No implementado |

Los contextos se acoplan por **ID de item del pipeline** y paths bajo `data/tenants/{tenant_id}/procesos/`, no por un mega-schema único. Una **oportunidad** es una decisión de interés (`interest_status=opportunity`), no el nombre del objeto base.

**Multi-usuario:** ver [MULTI_TENANCY.md](MULTI_TENANCY.md) — un despliegue, subdirectorios por tenant (settings, seace, procesos, agent), sin contenedor por usuario.

---

## Componentes actuales

```
tender_workflows/
  apps/portal/seace_monitor/
    scanner.py          # Worker multi-entidad/cliente: listado + ficha JSF
    client.py           # Cliente SEACE (ViewState, paginación)
    adp_*.py            # Fuente ADP (scanner/watchlist/downloader/client/parser)
    ingest/             # Registry/adapters: HOY solo metadatos (source/label/capabilities);
                        #   el contrato de comportamiento es objetivo → INGEST_CONTRACT.md
    analysis/
      runner.py         # download() + analyze()
      fast_reader.py    # Multi-PDF → Gemini free reader
      document_prep.py  # ZIP/RAR, LibreOffice, merge PDF fallback
      tender_bridge.py  # Puente run_step1_to_1_3 + eje 0
    web/                # FastAPI + templates Jinja
    process_storage.py  # Descarte, limpieza disco/BD
  instrucciones/        # Runbooks A–D (ver STAGES.md)
  scripts/              # Pipeline determinístico etapa C
  deploy/               # Docker Compose VPS + .env
  data/                 # Gitignored: BD, procesos, artifacts
```

### Despliegue producción (VPS)

| Servicio | Rol |
|----------|-----|
| `web` | UI en `:8080`, jobs background descarga/análisis |
| `worker` | `python -m seace_monitor scan` cada `poll_interval` |
| Volumen `tender_data` | SQLite + `data/tenants/{tenant_id}/` (hoy `default`) |

Secrets: `deploy/.env` (`GEMINI_API_KEY`, `SEACE_HTTP_PROXY`).

**Hermes (opcional):** un gateway, mount del mismo `/data`; no un contenedor por usuario. Ver [HERMES_VPS.md](HERMES_VPS.md), [MULTI_TENANCY.md](MULTI_TENANCY.md).

---

## Flujo operativo actual (SEACE)

```mermaid
stateDiagram-v2
  [*] --> publicada: scan detecta proceso
  publicada --> descartada: descartar / regla futura
  publicada --> autorejected: regla automática
  autorejected --> publicada: restaurar / revisar
  publicada --> descargando: Descargar
  descargando --> descargada: OK
  descargando --> publicada: fallo descarga
  descargada --> analizada: Analizar (PDFs seleccionados)
  descargada --> descartada: descartar
  analizada --> portafolio: marcar interés
  analizada --> descartada: descartar
  portafolio --> analizada: quitar portafolio
  descartada --> publicada: restaurar (sin archivos)
  descartada --> descargada: restaurar (con archivos)
```

### Rutas del portal

| Ruta | Estados |
|------|---------|
| `/publicaciones` | `publicada`, `descargando` |
| `/descargados` | `descargada` — selección multi-archivo + Analizar |
| `/analizados` | `analizada`, `portafolio` |
| `/descartados` | `descartada`, `autorejected` |

### Descarga

- Al pulsar **Descargar**: abre ficha SEACE en vivo → `documentos_json` (lista fresca) → Alfresco → ZIP descomprimido en `documentos/_extracted/`.
- El **scan no guarda** `documentos_json` (solo cronograma y metadatos de ficha).

### Análisis (fast-path, default en VPS)

1. Usuario marca N PDFs (default: archivos cuyo nombre contiene `bases` sin `anexo`).
2. Cada PDF se sube a **Gemini Files API**; una sola llamada `generateContent` con N partes + contexto SEACE.
3. Si falla multi-upload → fallback merge a un PDF.
4. Resultado: `free_reader_summary.md` + campos parseados en `analysis_results`.
5. Cronograma en UI viene de **ficha SEACE**, no del LLM.

### Análisis completo (alternativo, no default VPS)

`analysis.tender_procurement` → `run_step1_to_1_3.py` (LibreOffice, Modal Docling, eje 0). Convive con fast-path vía config.

---

## Modelo de datos (simplificado)

| Entidad | Campos clave |
|---------|--------------|
| `Entity` | RUC/ID, nombre, activa; conceptualmente entidad/cliente/comprador |
| `Process` | `source`, `source_ref`, `(entity_id, nid_proceso)` para SEACE, status, objeto, descripción, cronograma_json, data_dir, documentos_json |
| `AnalysisResult` | status, alcance, requisitos, raw_json, timestamps |

**Deuda de diseño acordada:** `Process` está hoy **sobrecargada** — es a la vez la fila del feed ruidoso (99% no interesa), el item de trabajo y el contenedor del análisis. El target la descompone en `FeedItem` (compartido, purgable) + `PipelineItem` (privado por tenant, identidad UUID estable, `ExternalRef` multi-canal) + overlay de decisiones por tenant. No renombrar a `Opportunity`: oportunidad será `interest_status=opportunity`. Próximos campos/ejes: `workflow_profile`, `interest_status`, `lifecycle_phase`, `trigger`/eventos y paquetes documentales. Detalle completo en [INGEST_CONTRACT.md](INGEST_CONTRACT.md); ver también [INPUT_SOURCES.md](INPUT_SOURCES.md) y [ROADMAP.md](ROADMAP.md).

---

## Integración LLM (hoy vs planificado)

| Uso | Implementación hoy | Plan |
|-----|-------------------|------|
| Fast-path análisis | `google-genai` directo, modelo en `config.yaml`; prompt por `source` | Prompt compuesto por entity/source/workflow/stage + UI Settings |
| Paso 1.3b eje 0 | Gemini vía tender_bridge | Misma abstracción |
| Agentes 1.5+ | Hermes/OpenClaw externo (Telegram/Discord) | Chat embebido en portal |

---

## SEACE — notas técnicas

- UI JSF/PrimeFaces; ficha requiere POST con ViewState.
- Clave de proceso: `(entity_id, nid_proceso)`.
- **Ver en SEACE:** proxy `/seace/open/{id}` con `link_id` fresco (el índice de fila en BD envejece).
- ONGEI: param `anio` en URL no filtra; el scan usa `config.anio` + primera página por entidad (`max_pages: 1` por defecto).
- Ficha refresh periódico (`ficha_refresh_interval`) para cronograma aunque el listado no cambie.

---

## Referencias

- [INGEST_CONTRACT.md](INGEST_CONTRACT.md) — arquitectura objetivo: contrato de plugins, feed/pipeline, multi-tenant por capas
- [ROADMAP.md](ROADMAP.md) — fases y prioridades
- [INPUT_SOURCES.md](INPUT_SOURCES.md) — fuentes, entrypoints, triggers y perfiles de lectura
- [INTEGRATION.md](INTEGRATION.md) — detalle Paso 1 ↔ portal
- [STAGES.md](STAGES.md) — etapas A→D
- [instrucciones/C_conversion/](../instrucciones/C_conversion/) — runbook conversión
- [instrucciones/D_portafolio/](../instrucciones/D_portafolio/) — runbook agentico
- [apps/REVIEW.md](../apps/REVIEW.md) — hallazgos de revisión de código
