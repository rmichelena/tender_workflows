# 01_workflow.md — Runbook operativo de procurement

> **Regla general**: para cada subpaso, ejecutar exactamente lo indicado: Owner, Prompt, Inputs, Modelo/Tools, Outputs, QA/Gate, Criterio Done.
> Selección de modelo/tool: desde `catalog_modelos.md` y `catalog_tools.md`, cumpliendo diversidad (no repetir mismo modelo en subagentes paralelos del mismo paso) y rotación (no repetir modelo+tool en reintentos). Parámetros operativos desde `params.yaml`.
> **JSON como canónico**: cualquier paso que produce datos estructurados (BOMs, item packs, specs, resultados, matrices, consolidado) entrega JSON. El orquestador genera los derivados (TSV, MD, XLSX) automáticamente.

---

## Gate 0 — Inputs humanos iniciales (obligatorio antes de ejecutar)

**Owner**: Orquestador (diálogo con humano)
**Acción**: Solicitar y registrar:
- `origen_fabricacion`: países permitidos | vetados | sin preferencia
- `marcas`: preferidas | vetadas | sin preferencia
- `docs_modo`: SIMPLE | COMPLEJO (para pasos 1.1–1.3)

**Output**: `/proyecto/overlay_usuario.yaml`
**Done**: overlay guardado, confirmado por humano.

---

## Paso 1 — Normalización documental

### 1.1–1.3 Convertir documentos fuente a Markdown

**Owner**: Orquestador → lanza Subagente(s) OCR
**Prompt**: `prompts/prompt_ocr_vision.md`
**Inputs**: cada archivo de `/proyecto/inputs/` (EETT, anexos, aclaraciones). Una invocación por archivo.
**Modelo**: Seleccionar del catálogo → función "OCR/Visión documental" (ej. Gemini 2.5 Pro, GPT-5.5, GLM-5V Turbo)
**Tools**: N/A (procesamiento documental, no búsqueda web)
**Timeout**: `params.yaml → timeouts.default`

- **Modo SIMPLE** (según `overlay_usuario.yaml`): lanzar 1 subagente por documento.
- **Modo COMPLEJO**: lanzar 2 subagentes por documento (modelos distintos, ambos con función OCR/Visión). Post-proceso: el orquestador hace diff entre ambas versiones y produce la versión consolidada resolviendo discrepancias.

**Outputs**: `/proyecto/artifacts/step_1_normalizados/{nombre_doc}.md`
**Gate 1**: Pausar. Presentar markdowns al humano para spot-check. Si hay errores graves, relanzar con otro modelo.
**Done**: todos los documentos de `inputs/` tienen su `.md` validado por humano.

---

### 1.4 Incorporar aclaraciones → documentos "aclarados"

**Owner**: Orquestador → 2 subagentes (Ejecutor + Auditor)

**Subagente Ejecutor**:
- Prompt: `prompts/prompt_merge_aclaraciones_ejecutor.md`
- Modelo: función "Reasoning" (ej. GPT-5.4, GLM-5.1, GPT-5.5)
- Inputs: EETT/anexos en MD (de step_1) + aclaraciones en MD (de step_1)
- Output: `/proyecto/artifacts/step_1_aclaradas/{nombre_doc}_aclarada_v1.md`
- Timeout: `params.yaml → timeouts.default`

**Subagente Auditor** (modelo DISTINTO al ejecutor):
- Prompt: `prompts/prompt_merge_aclaraciones_auditor.md`
- Modelo: función "Auditor/Revisor" o "Reasoning largo" (distinto al ejecutor)
- Inputs: documento aclarado (output del ejecutor) + aclaraciones originales en MD + documento base en MD
- Output: `/proyecto/artifacts/step_1_aclaradas/auditoria_aclaraciones_{doc}.md`
- Timeout: `params.yaml → timeouts.default`

**Post-proceso**: si el auditor detecta gaps (aclaraciones no incorporadas o cambios huérfanos), el orquestador instruye al ejecutor para corregir y vuelve a auditar hasta cobertura 1:1.
**Gate 2**: presentar reporte de auditoría al humano. Esperar aprobación.
**Done**: auditoría OK (cobertura 1:1) + aprobación humana.

> **Regla post-1.4**: a partir de aquí, SOLO se usan los documentos "aclarados". Los documentos de aclaración originales ya no se pasan como input a ningún paso posterior.

---

## Paso 2 — Extracción de BOM (bienes + servicios) **con especificaciones en contexto**

> **Cambio respecto al diseño inicial**: para evitar pérdida de correlación entre el ítem y sus requisitos (p.ej. confundir "torre de soporte de antena" con "torre de soporte de baliza"), los subagentes de BOM extraen cada ítem **junto con los requisitos que aparecen en la sección donde se menciona**. El paso 3 deja de ser "extracción" y pasa a ser verificación + refinamiento + herencia.

### 2.1 BOM High-Level (3 variantes)

**Owner**: Orquestador → lanza 3 subagentes
**Prompt**: `prompts/prompt_bom_highlevel.md`
**Inputs**: EETT aclaradas + anexos aclarados (todos)
**Modelos**: 3 modelos distintos del catálogo → función "BOM/Extracción" (ej. Kimi K2.6, GLM-5.1, Gemini 2.5 Pro)
**Tools**: N/A
**Timeout**: `params.yaml → timeouts.default`
**Outputs canónicos**: `/proyecto/artifacts/step_2_bom/BOM_highlevel_var{1,2,3}.json`
**Derivados (auto)**: `BOM_highlevel_var{n}.tsv` y `.md`
**Done**: 3 variantes generadas en JSON.

### 2.2 Consolidar BOM High-Level

**Owner**: Orquestador (ejecución directa, sin subagentes)
**Tarea**: contrastar las 3 variantes JSON entre sí y contra las EETT aclaradas. Verificar que no falte ni sobre nada. Producir consolidado con agrupaciones/clasificaciones, preservando los requisitos en contexto.
**Inputs**: 3 variantes JSON + EETT aclaradas
**Output canónico**: `/proyecto/artifacts/step_2_bom/BOM_highlevel_consolidado.json`
**Derivados (auto)**: `BOM_highlevel_consolidado.tsv` y `.md`
**Done**: consolidado completo, sin omisiones ni duplicados respecto a EETT.

### 2.3 BOM Exploded (4 variantes)

**Owner**: Orquestador → lanza 4 subagentes
**Prompt**: `prompts/prompt_bom_exploded.md`
**Inputs**:
- Subagentes 1 y 2: EETT aclaradas + BOM High-Level consolidado JSON
- Subagentes 3 y 4: EETT aclaradas solamente (sin BOM HL)

**Modelos**: 4 modelos distintos del catálogo → función "BOM/Extracción" (ej. GPT-5.4 Mini, Kimi K2.5 Turbo, DeepSeek V4 Pro, Qwen 3.6 Plus)
**Variable en prompt**: `{INCLUYE_BOM_HL: true|false}`
**Timeout**: `params.yaml → timeouts.default`
**Outputs canónicos**: `/proyecto/artifacts/step_2_bom/BOM_exploded_var{1,2,3,4}.json`
**Derivados (auto)**: `.tsv` y `.md`
**Done**: 4 variantes generadas en JSON, cada una con sus requisitos en contexto por ítem.

### 2.4 Consolidar BOM Exploded

**Owner**: Orquestador (ejecución directa)
**Tarea**: contrastar las 4 variantes JSON entre sí y contra EETT aclaradas. Desagregar todo accesorio como ítem separado. Verificar completitud. Preservar los requisitos extraídos en contexto por cada ítem.
**Inputs**: 4 variantes JSON + EETT aclaradas
**Output canónico**: `/proyecto/artifacts/step_2_bom/BOM_exploded_consolidado.json`
**Derivados (auto)**: `.tsv` y `.md`
**Done**: BOM exploded final, desagregado, sin omisiones, con requisitos en contexto por cada ítem.

### 2.5 Item Pack — Generar un archivo JSON+MD por ítem (determinista)

**Owner**: Orquestador (ejecución directa, 1 pasada — tarea mecánica)
**Prompt**: `prompts/prompt_item_pack_from_bom.md` (referencia de estructura)
**Inputs**: `BOM_exploded_consolidado.json`
**Tarea**: por cada ítem del BOM exploded, generar:
- Un archivo canónico JSON: estructura de metadata + requisitos extraídos en contexto (heredados del BOM)
- Un derivado MD para vista humana

**Outputs**:
- Canónicos: `/proyecto/artifacts/step_2_5_items/ITEM-{id}.json` (uno por ítem)
- Derivados (auto): `ITEM-{id}.md`
**Done**: existe un `.json` (+ `.md` derivado) por cada ítem del BOM exploded.

---

## Paso 3 — Verificación, refinamiento y herencia de especificaciones

> **Cambio de rol**: este paso ya no extrae specs desde cero (eso lo hizo el paso 2). Ahora verifica completitud, normaliza, clasifica hard/soft, y resuelve herencia entre ítems padre/hijo.

### 3.1 Verificar y completar specs por ítem (pasada 1)

**Owner**: Orquestador → lanza Subagente Specs
**Prompt**: `prompts/prompt_specs_verificacion_herencia.md`
**Modelo**: función "Reasoning + Contexto largo" (ej. GPT-5.5, Gemini 2.5 Pro)
**Inputs**:
- Archivos `ITEM-{id}.json` (del paso 2.5, ya con requisitos en contexto extraídos del BOM)
- EETT aclaradas completas
**Tarea del subagente**:
1. Para cada ítem, **verificar** que los requisitos extraídos en contexto (paso 2) están completos: recorrer la sección correspondiente de las EETT y confirmar que no falta ningún requisito explícito.
2. **Normalizar** formato y clasificar cada requisito como `hard` ("deberá/obligatorio/valor cuantitativo explícito") o `soft` ("preferentemente/deseable").
3. **Resolver herencia**: si un ítem tiene `parent_id`, evaluar qué requisitos del padre aplican técnicamente (ej. antena hereda banda de frecuencia de su radio). Incorporarlos como `HEREDADO`.
4. Mantener el texto **verbatim** y la **trazabilidad** (documento + sección + página) de cada requisito.

**Outputs canónicos**: `/proyecto/artifacts/step_3_specs/ITEM-{id}_specs.json`
**Derivados (auto)**: `ITEM-{id}_specs.md`
**Timeout**: `params.yaml → timeouts.default`
**Done**: todos los ítems tienen specs verificadas, normalizadas, con herencia resuelta y trazabilidad completa.

### 3.2 Revisión "ojos frescos" (pasada 2)

**Owner**: Orquestador → lanza Subagente Revisor
**Prompt**: `prompts/prompt_specs_revisor.md`
**Modelo**: distinto al usado en 3.1 (regla "revisor ≠ productor"). Ej.: si 3.1 fue GPT-5.5, usar Gemini 2.5 Pro o Kimi K2.6.
**Inputs**: ítems con specs (output JSON de 3.1) + EETT aclaradas
**Tarea**: verificar que no falten requisitos, que la herencia sea correcta, que las citas sean precisas, que la clasificación hard/soft sea coherente. Reportar gaps o errores.
**Output**: `/proyecto/artifacts/step_3_specs/revision_specs.md` + correcciones a ítems si aplica
**Timeout**: `params.yaml → timeouts.default`
**Post-proceso**: si hay correcciones, el orquestador las aplica a los `ITEM-{id}_specs.json` (regenerando los `.md` derivados).
**Done**: revisión OK, sin gaps críticos.

---

## Paso 4 — BOM "para búsqueda" (solo bienes, sin cantidades) + QA

**Owner**: Orquestador (ejecución directa + QA interno)
**Prompt**: `prompts/prompt_bom_para_busqueda.md`
**Inputs**: `BOM_exploded_consolidado.json` + ítems con specs finales (paso 3, JSON)
**Tarea**:
- Filtrar: solo bienes (quitar servicios: instalación, configuración, capacitación, etc.)
- Quitar cantidades
- Limpiar referencias no-buscables (ej. "según autorización MTC", "conectar a Redap Corpac") y reemplazarlas por especificaciones técnicas equivalentes que un agente de búsqueda pueda usar
- Mantener specs como parámetros técnicos buscables

**Output canónico**: `/proyecto/artifacts/step_4_busqueda/BOM_busqueda.json`
**Derivados (auto)**: `BOM_busqueda.tsv`
**QA**: verificar que (a) no quedaron servicios, (b) no quedaron cantidades, (c) no quedaron referencias no-buscables, (d) todos los bienes del BOM exploded están presentes.
**Done**: BOM búsqueda limpio y verificado.

---

## Paso 5 — Confirmar/actualizar preferencias del usuario

**Owner**: Orquestador (diálogo con humano)
**Tarea**: presentar el BOM búsqueda al humano. Preguntar si hay cambios o adiciones a las preferencias de origen/marca capturadas en Gate 0. Actualizar `overlay_usuario.yaml` si corresponde.
**Done**: overlay confirmado/actualizado.

---

## Paso 6 — Búsqueda de equipamiento (3 niveles, por ítem)

**Arquitectura**:

```
Orquestador
  → Subagente-Item (1 por ítem, en batches de batch_size_items_step6)
      → Search-Worker A (modelo X + tool Y)
      → Search-Worker B (modelo Z + tool W)
```

### 6.1 Lanzar Subagentes-Item (en batches)

**Owner**: Orquestador → lanza Subagentes-Item
**Prompt**: `prompts/prompt_item_manager.md`
**Modelo**: función "Subagente-Item" (ej. GLM-5.1, Kimi K2.6, GPT-5.5)
**Inputs por subagente-item**:
- `ITEM-{id}_specs.json` (con todos los requisitos hard/soft + herencia ya resuelta)
- `overlay_usuario.yaml` (restricciones origen/marca)
- `formato_matriz_cumplimiento.md` (formato obligatorio)
- `catalog_modelos.md` y `catalog_tools.md` (para seleccionar workers)
- `params.yaml` (timeouts, reintentos, combos sugeridos)

**Timeout subagente-item**: `params.yaml → timeouts.item_manager` (1500s = 25 min)
**Batch size**: `params.yaml → batching.batch_size_items_step6` (3 ítems simultáneos = 3 subagentes-item activos)

### 6.2 Lógica interna del Subagente-Item

(Se define en detalle en `prompt_item_manager.md`. Resumen:)

1. Lanzar 2 Search-Workers con modelos y tools distintos:
   - Worker A: modelo + search provider del combo `intento_1.worker_a`
   - Worker B: modelo + search provider del combo `intento_1.worker_b`
   - Prompt workers: `prompts/prompt_search_worker.md`
   - Timeout workers: `params.yaml → timeouts.search_worker` (600s = 10 min)
2. Recibir candidatos de ambos workers.
3. Validar cada candidato (con su propia búsqueda/verificación):
   - Confirmar producto vigente (página de fabricante activa, sin mención EOL)
   - Verificar cumplimiento de CADA requisito hard contra fuente primaria (datasheet/manual fabricante)
   - Clasificar: VÁLIDO | CONDICIONADO | DESCARTADO
4. Si no hay ≥1 candidato válido: relanzar (hasta `params.yaml → limits.max_relanzamientos_item` veces) con:
   - Exclusiones dinámicas (modelos que fallaron)
   - Rotación a combos `intento_2`, `intento_3` de `params.yaml`
5. Producir output:
   - JSON canónico del resultado: `ITEM-{id}_resultado.json` con candidatos, clasificación y log de búsqueda
   - Derivado MD: `ITEM-{id}_resultado.md`
   - Una matriz de cumplimiento por candidato Válido/Condicionado, en JSON (`ITEM-{id}_candidato_{n}.json`) y MD derivado, según `formato_matriz_cumplimiento.md`.
6. Si tras todos los reintentos no hay candidato válido: reportar SIN_CANDIDATO + diagnóstico.

### 6.3 Recepción y Gate

**Owner**: Orquestador
**Outputs**:
- `/proyecto/artifacts/step_6_resultados/items/ITEM-{id}_resultado.json` + `.md` (uno por ítem)
- `/proyecto/artifacts/step_6_resultados/matrices/ITEM-{id}/ITEM-{id}_candidato_{n}_{marca}_{modelo}.json` + `.md` (uno por candidato Válido/Condicionado)

**Gate 4**: para ítems con estado SIN_CANDIDATO: pausar, presentar diagnóstico al humano, esperar decisión (relajar requisito / aceptar condicionado / buscar manualmente).
**Done**: todos los ítems tienen ≥1 candidato válido, o decisión humana documentada.

---

## Paso 7 — Consolidación final + QA

### 7.1 Producir consolidado

**Owner**: Orquestador (ejecución directa)
**Prompt**: `prompts/prompt_consolidacion_paso7.md`
**Inputs**:
- `BOM_busqueda.json` (paso 4)
- Todos los `ITEM-{id}_resultado.json` (paso 6)
- Todas las matrices `ITEM-{id}_candidato_{n}.json` (paso 6)

**Tarea**: producir el consolidado canónico en formato "long" (una entrada por candidato). Conformidad con `schemas/consolidado_row.schema.json`.

**Outputs**:
- Canónico: `/proyecto/outputs/consolidado.json`
- Derivados (auto):
  - `/proyecto/outputs/consolidado.tsv` (tabular para procesamiento)
  - `/proyecto/outputs/consolidado.md` (lectura humana)
  - `/proyecto/outputs/consolidado.xlsx` (con filtros y hoja de resumen, sin celdas combinadas)

### 7.2 QA Final

**Owner**: Orquestador → lanza Subagente QA
**Prompt**: `prompts/prompt_QA_final.md`
**Modelo**: función "Auditor/Revisor", distinto del que produjo 7.1
**Checks**:
- Conteo de ítems = total de bienes en BOM búsqueda
- Cada ítem tiene ≥1 candidato (o decisión humana documentada para SIN_CANDIDATO)
- Todos los campos obligatorios están llenos en el JSON canónico
- URLs de evidencia presentes y bien formateadas
- Notas presentes para candidatos condicionados
- Consistencia entre `ITEM-{id}_resultado.json`, matrices y tabla consolidada
- Existencia de los archivos de matriz referenciados

**Output**: `/proyecto/outputs/QA_report.md`
**Done**: QA OK. Entregable final listo.

---

## Resumen de artefactos preservados

```
proyecto/
├── inputs/                          (fuentes originales, read-only)
├── overlay_usuario.yaml             (preferencias)
├── artifacts/
│   ├── step_1_normalizados/         (markdowns de docs fuente)
│   ├── step_1_aclaradas/            (docs aclarados + auditoría)
│   ├── step_2_bom/                  (variantes + consolidados HL/exploded en JSON+TSV+MD)
│   ├── step_2_5_items/              (1 JSON+MD por ítem, base estructurada)
│   ├── step_3_specs/                (ítems verificados + revisión)
│   ├── step_4_busqueda/             (BOM búsqueda en JSON+TSV)
│   └── step_6_resultados/
│       ├── items/                   (resultado por ítem en JSON+MD)
│       └── matrices/ITEM-XXX/       (matrices por candidato en JSON+MD)
├── outputs/
│   ├── consolidado.json             (canónico)
│   ├── consolidado.tsv              (derivado tabular)
│   ├── consolidado.md               (derivado legible)
│   ├── consolidado.xlsx             (derivado Excel)
│   └── QA_report.md
└── logs/
    └── decision_log.md              (modelos usados, reintentos, escalamientos)
```
