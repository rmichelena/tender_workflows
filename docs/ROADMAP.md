# Roadmap — tender_workflows

Roadmap del producto integrado (portal + ingesta + análisis + agentes).  
**Arquitectura:** [ARCHITECTURE.md](ARCHITECTURE.md).

Última actualización: mayo 2026.

---

## Leyenda

| Símbolo | Significado |
|---------|-------------|
| ✅ | Hecho en producción o mergeado |
| 🔄 | En curso / usable con limitaciones |
| 📋 | Planificado, diseño acordado |
| 🔮 | Largo plazo |

---

## Estado actual (baseline ✅)

| Capacidad | Notas |
|-----------|-------|
| Scan SEACE multi-entidad | Worker 6h, savepoints, ficha refresh |
| Portal publicaciones → descargados → analizados | Estados separados descarga/análisis |
| Descarga Alfresco + ZIP/RAR | `documentos_json` solo al descargar |
| Análisis multi-PDF fast-path | Gemini 2.5 Pro, selección en UI |
| Portafolio manual | Sin botón «Continuar agente» aún |
| VPS Docker | `bots.infinitek.pe:8080`, web + worker |
| Descarte con limpieza disco/BD | Incluye `documentos_json`, `AnalysisResult` |
| Proxy Ver en SEACE | Server-side ViewState |
| Puente Paso 1 completo | Existe; no es el camino default en VPS |

---

## Fase 1 — Cerrar el ciclo humano en el portal 📋

**Objetivo:** de scan a portafolio sin salir del navegador; puente hacia agentes.

| Ítem | Descripción | Prioridad |
|------|-------------|-----------|
| 1.1 | Botón **Continuar extracción** en portafolio → sesión Hermes con expediente precargado | Alta |
| 1.2 | Chat embebido (Hermes gateway u Open WebUI acoplado) | Alta |
| 1.3 | Errores visibles: descarga/análisis fallido con mensaje en UI | Media |
| 1.4 | Modo **Análisis completo** opcional (Paso 1.0–1.3 + eje 0) además de fast-path | Media |
| 1.5 | Traefik + `licitaciones.infinitek.pe` | Baja |

**Definition of done:** proceso en portafolio → chat en portal → agente arranca Paso 1.5 con contexto del expediente.

---

## Fase 2 — Settings y providers LLM 📋

**Objetivo:** configurar modelos sin editar YAML ni redeploy.

| Ítem | Descripción |
|------|-------------|
| 2.1 | Pantalla **Settings** en portal (admin) |
| 2.2 | Providers: **Google GenAI**, **OpenRouter**, **Fireworks** |
| 2.3 | Modelo por tarea: fast-path reader, eje 0, visión planos (1.2b), agentes |
| 2.4 | API keys por provider (env + override en BD cifrado o secrets manager) |
| 2.5 | Abstracción `LLMProvider` en código; fast_reader y tender_bridge consumen la misma interfaz |

**Notas de diseño:**

- Routing inicial puede ser YAML en BD; UI edita la misma estructura.
- Reutilizar ideas de `instrucciones/model_routing.yaml` pero scoped al portal.
- Dependencia con **multiusuario** (Fase 4): settings globales vs por tenant.

---

## Fase 3 — Prefiltros heurísticos y auto-análisis 📋

**Objetivo:** reducir ruido en publicaciones y automatizar casos de alto valor.

### 3.1 Descarte automático (post-scan)

Reglas sobre campos del **listado/ficha** (sin descargar documentos):

| Regla ejemplo | Acción |
|---------------|--------|
| `objeto = servicio` **y** `descripción` contiene `legal` | → `descartada` + motivo |
| `descripción` contiene `reparto` | → `descartada` + motivo |
| Keywords configurables por entidad | → `descartada` |

**Implementación propuesta:**

- Tabla o YAML `filter_rules` con: condiciones (campo, operador, valor/patrón), acción, prioridad, activo.
- Motor evalúa tras upsert en scanner; persiste `discard_reason` en `Process`.
- UI en descartados muestra motivo; reglas editables en Settings (Fase 2).

### 3.2 Auto-descarga + auto-análisis

Reglas compuestas que disparan pipeline sin intervención:

| Regla ejemplo | Acción |
|---------------|--------|
| `objeto = bien` **y** `descripción` contiene `rayos x` **y** `descripción` **no** contiene (`hospital` **or** `salud`) | → descargar → analizar (PDFs default: bases + anexos según política) |
| Perfil por línea de negocio (equipos médicos vs industrial) | → distintos conjuntos de reglas |

**Implementación propuesta:**

- Misma DSL de reglas; acción `auto_pipeline` con pasos: `download`, `analyze`, `notify`.
- Cola de jobs con límite de concurrencia y presupuesto LLM diario.
- Opt-in por entidad; log de qué regla disparó cada proceso.
- Requiere prefiltros de descarte para no quemar API en basura.

### 3.3 SEACE estado en listado 🔮

- Trackear estado SEACE (desierta, cancelada, …) para descartar o archivar sin reglas de texto.

---

## Fase 4 — Multiusuario (overhaul mayor) 📋

**Objetivo:** varios usuarios/equipos con permisos, auditoría y aislamiento.

| Ítem | Descripción |
|------|-------------|
| 4.1 | Auth (sessions/OAuth); login en portal |
| 4.2 | Roles: admin, analista, viewer |
| 4.3 | Entidades visibles por usuario/equipo |
| 4.4 | Acciones auditadas (descargar, analizar, descartar, portafolio) |
| 4.5 | API keys LLM y reglas: scope global vs por organización |
| 4.6 | Migración schema: `users`, `organizations`, `memberships` |

**Impacto:** toca casi todas las rutas web, BD, deploy y Settings. Planificar antes de segundo cliente externo.

**Preparación ya acordada:** `Process` → `Opportunity` + `source`; no acoplar nombres SEACE al core.

---

## Fase 5 — Canales de comunicación 📋

**Objetivo:** notificaciones y comandos fuera del navegador.

| Canal | Uso | Notas |
|-------|-----|-------|
| **WhatsApp** | Alertas nuevas publicaciones, resumen análisis, confirmar portafolio | API ya existe en otro proyecto — integrar vía adapter HTTP |
| Telegram / Discord | Mantener para desarrollo agentes | Hermes/OpenClaw |
| Email | Forward de invitaciones directas (entrypoint 3) | 🔮 Fase 6 |

**Integración WhatsApp (borrador):**

```mermaid
sequenceDiagram
  participant Worker
  participant Portal
  participant WA as WhatsApp API
  participant User

  Worker->>Portal: nuevo proceso / auto-análisis done
  Portal->>WA: plantilla + link portal
  WA->>User: mensaje
  User->>WA: comando opcional
  WA->>Portal: webhook
```

- Webhook en portal o microservicio ligero.
- Plantillas: «Nueva LP {nomenclatura} — fin consultas {fecha}» + deep link.
- No duplicar lógica de reglas; WhatsApp es **canal de salida/entrada**, no motor de reglas.

---

## Fase 6 — Multi-ingesta y perfiles de workflow 🔮

| Ítem | Descripción |
|------|-------------|
| 6.1 | Adapter email (forwards → `inputs/`) |
| 6.2 | Segundo portal de licitación |
| 6.3 | Upload manual de expediente |
| 6.4 | Workflow profiles: `pe_public`, `market_study`, `multilateral` |
| 6.5 | `workflows/profiles/*.yaml` referenciando `instrucciones/` |

---

## Fase 7 — Catálogo, propuesta, finanzas 🔮

| Contexto | Entregable |
|----------|------------|
| **Catalog** | SKUs propios, specs, costos → alimenta Paso 6 |
| **Proposal** | Generación propuesta desde BOM + catálogo |
| **Finance** | Flujo de caja vs cronograma contractual |

Puente natural: ítems BOM (`ITEM-{id}`) → `catalog_item_id` nullable.

---

## Orden de ejecución recomendado

```mermaid
gantt
  title Roadmap tender_workflows
  dateFormat YYYY-MM
  section Corto plazo
  Fase 1 Portal + Hermes     :2026-05, 2M
  Fase 3a Prefiltros descarte :2026-06, 1M
  section Medio plazo
  Fase 2 Settings LLM        :2026-06, 2M
  Fase 3b Auto-análisis      :2026-07, 2M
  Fase 5 WhatsApp            :2026-07, 1M
  section Mayor
  Fase 4 Multiusuario        :2026-08, 3M
  section Largo
  Fase 6 Multi-ingesta       :2026-10, 4M
  Fase 7 Catálogo            :2027-01, 6M
```

1. **Fase 1** — valor inmediato; conecta lo que ya funciona con agentes.
2. **Fase 3a** — prefiltros descarte (barato, reduce ruido ~900 → actionable).
3. **Fase 2** — settings antes de escalar auto-análisis (control de costos/modelos).
4. **Fase 3b** — auto-pipeline con reglas de negocio (rayos X, etc.).
5. **Fase 5** — WhatsApp cuando haya eventos worth pushing.
6. **Fase 4** — cuando necesites más de un operador o cliente.
7. **Fases 6–7** — según segundo portal o línea de producto.

---

## Backlog técnico (de REVIEW.md)

Items de robustez no productizados; abordar cuando toque el área:

| ID | Tema |
|----|------|
| M5 | Warning si SEACE tiene más páginas que `max_pages` |
| 1.2b | Gemini real para planos (hoy `auto_leave`) |
| H2 | ✅ Mitigado con `ficha_refresh_interval`; revisar TTL |

Ver [apps/REVIEW.md](../apps/REVIEW.md).

---

## Cómo contribuir a este documento

- Nuevas ideas → sección de fase correspondiente + línea en tabla.
- Ítem completado → mover a **Estado actual** con ✅.
- Cambios de arquitectura → actualizar [ARCHITECTURE.md](ARCHITECTURE.md) en el mismo PR.
