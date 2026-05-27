# Fuentes, entrypoints y perfiles de lectura

Documento de diseño para multi-ingesta: portales públicos/privados, email, alta manual, triggers externos y cómo esas dimensiones afectan el free reader y el camino hacia portafolio.

**Relacionado:** [STAGES.md](STAGES.md), [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md).

---

## Principio central

El objeto base que entra al sistema no debe llamarse todavía **oportunidad**. Muchas publicaciones, invitaciones, correos o documentos descargados son ruido, material preliminar o items que requieren análisis antes de decidir interés comercial.

El concepto base es **PipelineItem**: una unidad que llegó por algún canal y puede avanzar por estados operativos (`publicada`, `descargada`, `analizada`, `portafolio`, etc.). **Oportunidad** es una dimensión de interés que se marca después, en cualquier etapa.

---

## Conceptos canónicos

| Concepto | Qué significa |
|----------|---------------|
| `PipelineItem` | Unidad base de ingesta/procesamiento. En código hoy vive como `Process`; el renombre es deuda futura. |
| `Entity` | Entidad/cliente/comprador. Incluye entidades públicas SEACE y clientes privados como Aeropuertos del Perú o Aeropuertos Andinos. |
| `source` | Origen real del item: `seace`, `adp_portal`, `aap_portal`, `email`, `manual`, etc. |
| `source_ref` | Referencia estable dentro del origen: `nid_proceso`, URL/ficha del portal, `Message-ID`, código interno, etc. |
| `trigger` | Qué provocó revisar el origen: `scheduled_scan`, `manual_create`, `mailbox_poll`, `change_detection_webhook`. No reemplaza a `source`. |
| `workflow_profile` | Ruta esperada de trabajo: `public_tender`, `private_tender`, `market_study`, `manual_rfp`, `multilateral`, etc. |
| `interest_status` | Estado de interés comercial: `none`, `watching`, `candidate`, `opportunity`, `rejected`. Independiente del estado operativo. |
| `status` | Estado operativo del portal: `publicada`, `descargando`, `descargada`, `analizada`, `portafolio`, `autorejected`, `archivada`, `descartada`. |
| `stage` | Etapa de procesamiento A–D: pre-portafolio, staging, conversión, trabajo agentico. |

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
- `source_ref` puede ser `Message-ID` o un identificador estable del hilo.
- `workflow_profile=market_study` suele ser el default.
- El sistema debe intentar asociar nuevos correos/documentos con un `PipelineItem` existente antes de crear uno nuevo.

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

## Implicancias técnicas próximas

Cambios de modelo/dominio:

- Mantener `Process` por compatibilidad inmediata, pero documentarlo como `PipelineItem` conceptual.
- No renombrar a `Opportunity`: oportunidad es `interest_status=opportunity`.
- Ampliar `Entity` a entidad/cliente/comprador, con campos públicos/privados cuando toque.
- Agregar `workflow_profile` a `Process`/`PipelineItem`.
- Agregar `interest_status` con valores `none`, `watching`, `candidate`, `opportunity`, `rejected`.
- Separar `trigger` de `source`.

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
