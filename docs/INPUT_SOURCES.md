# Fuentes, entrypoints y perfiles de lectura

Documento de diseño para multi-ingesta: portales públicos/privados, email, alta manual, triggers externos y cómo esas dimensiones afectan el free reader y el camino hacia portafolio.

**Relacionado:** [INGEST_CONTRACT.md](INGEST_CONTRACT.md) (contrato de plugins + feed/pipeline + multi-tenant — arquitectura objetivo), [STAGES.md](STAGES.md), [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md).

> Este documento describe **conceptos de dominio** (fuentes, perfiles, paquetes). El
> **contrato técnico** que los implementa como plugins (interfaz `SourceAdapter`, split
> feed/pipeline, identidad estable, capas multi-tenant) vive en [INGEST_CONTRACT.md](INGEST_CONTRACT.md).

---

## Principio central

El objeto base que entra al sistema no debe llamarse todavía **oportunidad**. Muchas publicaciones, invitaciones, correos o documentos descargados son ruido, material preliminar o items que requieren análisis antes de decidir interés comercial.

De hecho hay **dos objetos distintos** que hoy `Process` mezcla (ver [INGEST_CONTRACT.md](INGEST_CONTRACT.md)):

- **FeedItem** — lo descubierto por un canal (99% ruido). Barato, compartido entre tenants, purgable a los ~90 días.
- **PipelineItem** — lo que un usuario **decidió trabajar** (descargar/analizar/seguir). Privado por tenant, identidad estable, vive para siempre.

Un `PipelineItem` se crea al promover un `FeedItem` (acción positiva), copiando un snapshot **sin foreign key**. **Oportunidad** es una dimensión de interés (`interest_status=opportunity`) que se marca después, en cualquier etapa.

---

## Conceptos canónicos

| Concepto | Qué significa |
|----------|---------------|
| `PipelineItem` | Unidad base de ingesta/procesamiento. En código hoy vive como `Process`; el renombre es deuda futura. |
| `Entity` | Entidad/cliente/comprador. Incluye entidades públicas SEACE y clientes privados como Aeropuertos del Perú o Aeropuertos Andinos. |
| `source` | Origen real del item: `seace`, `adp_portal`, `aap_portal`, `email`, `manual`, etc. |
| `source_ref` | Referencia estable dentro del origen: `nid_proceso`, URL/ficha del portal, `Message-ID`, código interno, etc. Vive en `ExternalRef` (N por `PipelineItem`). |
| `ExternalRef` | Vínculo `(source, source_ref)` a un `PipelineItem`. Un mismo item puede tener varios (p. ej. `email` y luego `seace`) sin cambiar su identidad. |
| `trigger` | Qué provocó revisar el origen: `scheduled_scan`, `manual_create`, `mailbox_poll`, `change_detection_webhook`. No reemplaza a `source`. |
| `workflow_profile` | Ruta esperada de trabajo: `public_tender`, `private_tender`, `market_study`, `manual_rfp`, `multilateral`, etc. |
| `interest_status` | Estado de interés comercial: `none`, `watching`, `candidate`, `opportunity`, `rejected`. Independiente del estado operativo. |
| `status` | Estado operativo del portal: `publicada`, `descargando`, `descargada`, `analizada`, `portafolio`, `autorejected`, `archivada`, `descartada`. |
| `stage` | Etapa de **procesamiento documental** A–D: pre-portafolio, staging, conversión, trabajo agentico. |
| `lifecycle_phase` | **Fase comercial** del objeto: `pre_licitacion`/`estudio_mercado` → `licitacion` → `adjudicacion` → `ejecucion`. Ortogonal a `status` y `stage`. |

`portafolio` no debe ser sinónimo obligatorio de `opportunity`: normalmente implica interés, pero puede usarse para hacer análisis profundo antes de declarar una oportunidad.

`autorejected` es un estado operativo para items rechazados por reglas automáticas de filtro. No reemplaza a `interest_status=rejected`, que representa una decisión de interés comercial.

---

## Fuentes y entrypoints

### SEACE

- `source=seace`.
- Trigger actual: `scheduled_scan` del worker por entidades activas.
- El listado/ficha del portal aporta metadatos, cronograma y documentos Alfresco.
- El free reader **no** debe extraer cronograma del PDF como fuente principal; la UI usa `cronograma_json` de la ficha.

### Portales públicos/privados por cliente

Ejemplos iniciales: Aeropuertos del Perú, Aeropuertos Andinos y otros clientes con portales propios.

- Cada cliente/portal puede requerir watcher/parser/descarga diferente.
- `Entity` representa al cliente/comprador, no solo a una entidad pública.
- Los documentos suelen ser PDFs similares a SEACE, pero los metadatos viven en lugares distintos.
- En algunos portales, el valor referencial y el cronograma aparecen casi siempre en los documentos, no en el portal.
- El adapter debe enfocarse en extraer metadata mínima y descargar documentos; el free reader completa lo documental según perfil.

### Change Detection

Change Detection es un **trigger externo**, no un `source`.

Flujo esperado:

```text
Change Detection detecta cambio
→ webhook a tender_workflows
→ se identifica entity/source configurado
→ se dispara scan/fetch puntual del adapter real
→ el adapter crea/actualiza PipelineItem y paquetes documentales
```

Esto evita que `source=change_detection` oculte el origen real que importa para prompts, parsers y perfiles.

### Email / estudios de mercado

Casos típicos:

- Cliente público o privado envía solicitud preliminar de cotización.
- Adjunta especificaciones técnicas, EETT, anexos o una segunda versión.
- A menudo no hay cronograma, valor de contratación ni requisitos del postor.
- El valor está en avanzar hacia portafolio: extraer requerimientos, BOM, candidatos de producto, brechas y observaciones.

Para email:

- `source=email`.
- `source_ref` puede ser `Message-ID` o un identificador estable del hilo (un `source_ref` **sintético** por hash de remitente+asunto+fecha+adjuntos cuando no haya uno natural).
- `workflow_profile=market_study` suele ser el default.
- El sistema debe intentar asociar nuevos correos/documentos con un `PipelineItem` existente antes de crear uno nuevo.

#### De pre-licitación a licitación: NO se cambia el UID

Caso clave: algo que **llega por correo** (estudio de mercado, solicitud preliminar) y
**luego se publica** como licitación es el **mismo objeto de negocio**, solo en una fase
posterior.

- La identidad del `PipelineItem` es un **UUID interno inmutable**, no el `source_ref`.
- Cuando aparece la publicación, se **agrega un `ExternalRef`** (`source=seace`, `source_ref=<nid>`)
  al mismo item; **no se cambia el UID** ni se duplica.
- Avanza `lifecycle_phase`: `estudio_mercado` → `licitacion`. El **estudio de mercado no es
  un tipo de proceso separado**, es una fase temprana del mismo item.

Esto exige relajar el `UniqueConstraint(source, entity_id, source_ref)` actual de `Process`
(que forzaría dos filas) y mover la identidad a `PipelineItem` + `ExternalRef`. Detalle en
[INGEST_CONTRACT.md §3](INGEST_CONTRACT.md#identidad-estable-y-multi-canal-externalref).

### Alta manual

Alta manual es el fallback controlado:

- `source=manual`.
- Metadatos mínimos: entidad/cliente, título, referencia interna, descripción.
- Upload inicial de documentos.
- Debe reutilizar los mismos mecanismos de paquetes documentales y free reader por perfil.

---

## Paquetes documentales

Un `PipelineItem` puede recibir más de un paquete de documentos durante su vida. Por ahora se modela como estructura en disco/manifest; no requiere tabla desde el inicio.

Ejemplo conceptual:

```text
{pipeline_item_key}/
  packages/
    2026-05-27_initial_portal/
      manifest.json
      documentos/
    2026-06-03_email_specs_v2/
      manifest.json
      documentos/
    2026-06-10_clarifications/
      manifest.json
      documentos/
```

Tipos esperados:

- `initial_portal_docs`
- `initial_email_docs`
- `manual_upload`
- `technical_specs_v2`
- `clarifications`
- `answers_to_questions`
- `integrated_terms`
- `addendum`

Cada paquete debería registrar:

- origen (`source`, `source_ref`, `trigger`)
- timestamp de recepción
- tipo de paquete
- archivos y hashes
- si reemplaza, complementa o aclara paquetes previos
- si requiere reanálisis global o análisis incremental

---

## Free reader y prompts

El free reader no puede ser un prompt único. Debe resolverse por dimensiones:

```text
entity/client + source + workflow_profile + stage → reader_profile
```

Resolución actual recomendada: fallback jerárquico.

1. Perfil específico por `entity`/cliente.
2. Perfil específico por `source`.
3. Perfil por `workflow_profile`.
4. Perfil por `stage`.
5. Perfil default.

Roadmap: construir prompts dinámicamente por composición:

```text
prompt = base_prompt
       + source_rules
       + entity_rules
       + workflow_profile_rules
       + stage_rules
       + selected_sections
```

Ejemplos:

- SEACE: no pedir cronograma del PDF; el cronograma viene de ficha.
- Aeropuertos del Perú / Aeropuertos Andinos: pedir cronograma y valor referencial desde documentos cuando estén presentes.
- `market_study`: asumir que puede no existir cronograma, valor ni requisitos del postor; enfocar EETT, requerimientos, BOM inicial, compatibilidad con productos, dudas y observaciones.
- Etapa A: lectura rápida para decidir si avanzar.
- Etapa B/C/D: extracción más profunda, consolidación documental, BOM, candidatos, brechas y propuesta.

---

## Reglas de autoreject

Las reglas de descarte automático operan sobre items todavía `publicada`. Si una regla coincide, el item pasa a `status=autorejected` y se conserva el motivo para revisión/restauración desde `/descartados`.

La sintaxis inicial es deliberadamente parecida a búsquedas tipo Google:

- Palabras sin campo buscan en `descripcion + nomenclatura`.
- Comillas indican frase literal: `"transporte de personal"`.
- Espacios significan `AND`.
- `OR` permite alternativas.
- Paréntesis agrupan.
- `-` niega una condición.
- `campo:valor` limita a un campo; campos iniciales: `objeto`, `descripcion`, `nomenclatura`, `entidad`, `source`.
- Comparación normalizada: minúsculas y sin tildes.

Ejemplos:

```text
objeto:servicio (limpieza OR vigilancia OR seguridad)
objeto:servicio ("transporte de personal" OR "traslado de personal" OR movilidad) -entidad:corpac
objeto:bien (alimentos OR viveres OR raciones)
objeto:bien (uniformes OR vestimenta OR calzado)
objeto:servicio ("alquiler de vehiculos" OR "alquiler de camionetas" OR "alquiler de local")
```

Las reglas empaquetadas son un baseline conservador y deben poder moverse a configuración por tenant/cliente cuando exista Settings multiusuario.

---

## Implicancias técnicas próximas

Cambios de modelo/dominio (detalle y plan incremental en [INGEST_CONTRACT.md](INGEST_CONTRACT.md)):

- Descomponer `Process` en `FeedItem` (compartido, purgable) + `PipelineItem` (privado por tenant, UUID estable) + overlay de decisiones por tenant.
- Mover identidad a `PipelineItem` + `ExternalRef` (multi-canal sin cambiar UID).
- No renombrar a `Opportunity`: oportunidad es `interest_status=opportunity`.
- Ampliar `Entity` a entidad/cliente/comprador; la selección `activa` pasa a ser por tenant.
- Agregar `workflow_profile`, `interest_status` y el eje `lifecycle_phase`.
- Separar `trigger` de `source`.
- Sacar el autoreject del scanner: pasa a overlay por tenant sobre el feed compartido (no muta el item).

Cambios de procesamiento:

- Adapters por `source`.
- Triggers externos pueden llamar adapters existentes.
- Paquetes documentales versionados en manifest/disco.
- Reanálisis debe poder correr sobre paquete nuevo o conjunto vigente.
- Free reader debe seleccionar perfil por dimensiones y registrar perfil usado.

Cambios UI:

- Permitir marcar `interest_status` en cualquier item y etapa.
- No asumir que `portafolio` equivale a oportunidad.
- Mostrar entity como cliente/comprador en fuentes no-SEACE.
- Diferenciar “nuevo paquete/documentos nuevos” de “nuevo item”.

---

## Decisiones abiertas

- Nombre final en código para `PipelineItem` y calendario de renombre desde `Process`.
- Formato exacto de `manifest.json` por paquete documental.
- Heurísticas de matching para asociar correos/versiones nuevas al mismo item.
- Cuándo formalizar `DocumentPackage` como tabla.
- Cómo versionar prompts dinámicos por tenant/cliente.
