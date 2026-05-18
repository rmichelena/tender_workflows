# 01_workflow.md — Runbook operativo de procurement (v0.2)

> **Regla general**: para cada subpaso, ejecutar exactamente lo indicado: Owner, Tipo, Prompt, Inputs, Modelo/Tools, Tool budget, Outputs, QA/Gate, Criterio Done.
> Selección de modelo: desde `model_routing.yaml` (no de `catalog_modelos.md` directamente — ese describe capacidades; el routing decide quién hace qué basado en evidencia).
> Selección de tools: desde `catalog_tools.md`, respetando primario+fallback.
> Patrones de delegación: ver `agent_patterns.md` — leer antes de delegar.
> **JSON como canónico**: cualquier paso que produce datos estructurados entrega JSON validado contra schema. El orquestador genera los derivados (TSV, MD, XLSX) automáticamente.

## Cambios v0.2 vs v0.1

| Decisión v0.1 | Aprendizaje ICAO-00068 | Decisión v0.2 |
|---|---|---|
| 3 variantes BOM HL + consolidación | 28 requisitos FALTANTE por decisiones implícitas inconsistentes | **1 productor + 1 auditor "ojos frescos"** |
| 4 variantes BOM Exploded + consolidación | Idem + 504 timeout con 426 items | **1 productor + 1 auditor + scratchpad compartido con 2.1** |
| OCR Paso 1: subagente LLM-vision | LandingAI ADE funciona mejor | **Pipeline determinístico: DOCX→PDF + pdf_image_audit + LandingAI ADE** |
| Matriz cumplimiento dentro del search worker | Mezcla búsqueda + validación + estructuración | **LLM call separada post-búsqueda** |
| Solo Firecrawl | Sin créditos a mitad → degradación silenciosa | **Pool de tools con fallback explícito** (catalog_tools.md) |
| Solo español en queries | 0% hit rate | **Búsqueda bilingüe ES+EN obligatoria** |
| `max_tokens` alto | JSON truncado en kimi/deepseek/minimax | **Tool budget explícito + schema validation** |

---

## Gate 0 — Inputs humanos iniciales (obligatorio antes de ejecutar)

**Owner**: Orquestador (diálogo con humano)
**Acción**: Solicitar y registrar:
- `origen_fabricacion`: países permitidos | vetados | sin preferencia
- `marcas`: preferidas | vetadas | sin preferencia

**Output**: `/proyecto/overlay_usuario.yaml`
**Done**: overlay guardado, confirmado por humano.

> Nota v0.2: ya no se pregunta `docs_modo` (SIMPLE/COMPLEJO). El Paso 1 ahora es pipeline determinístico que maneja ambos casos uniformemente.

---

## Paso 1 — Normalización documental (pipeline determinístico)

**Tipo**: workflow no-LLM determinístico + LLM call selectiva como fallback.

### 1.1 Conversión a PDF (si aplica)

**Owner**: Orquestador (script determinístico)
**Tarea**: por cada archivo en `/proyecto/inputs/`:
- Si es DOCX → convertir a PDF (LibreOffice headless o equivalente).
- Si es ya PDF → pasar sin tocar.

**Output**: `/proyecto/artifacts/step_1_pdfs/{nombre_doc}.pdf`
**Done**: todos los inputs son PDF.

### 1.2 Optimizador (quitar headers/footers/firmas/sellos/decorativos)

**Owner**: Orquestador (script `scripts/pdf_image_audit.py` o similar)
**Tarea**: detectar y eliminar zonas repetitivas (headers, footers, watermarks, firmas, sellos, logos decorativos) que aparecen en múltiples páginas. Producir PDF "limpio".

**Output**: `/proyecto/artifacts/step_1_pdfs_clean/{nombre_doc}_clean.pdf`
**Done**: PDFs optimizados sin elementos repetitivos no útiles.


### 1.2b Detección, análisis y sustitución de planos/diagramas grandes

**Owner**: Orquestador + subagente visual barato.
**Script**: `scripts/pdf_plan_pages.py`
**Prompt visual**: `prompts/prompt_planos_vision.md`
**Schema**: `schemas/plan_pages_analysis.schema.json`
**Modelo**: `model_routing.yaml → paso_1_2b_planos_vision` (primary: `google/gemini-2.5-flash`).

**Tarea**: después del PDF limpio y antes de LandingAI/OCR, detectar páginas con tamaño anómalo respecto al tamaño dominante del documento. Estas páginas suelen ser planos, diagramas o anexos tabulares grandes. El tamaño solo genera candidatos; la confirmación la hace un modelo visual.

**Método**:
1. Auditar tamaño de páginas del PDF limpio.
2. Calcular tamaño dominante y área mediana.
3. Marcar candidatos por área/aspect ratio/tamaño absoluto, agrupando rangos consecutivos.
4. Rasterizar candidatos a resolución moderada.
5. Pedir al modelo visual confirmar si son planos/diagramas.
6. Si una página confirmada es plano/diagrama:
   - extraer `identifier_or_title` visible, ej. `Plano instalaciones eléctricas página 1`, `SPYL-SV-T-0300`, `SPUR-SV-T-0301 — Distribución de datos y voz en el terminal`;
   - describirla brevemente;
   - extraer información explícitamente visible útil para procurement;
   - marcar `exclude_from_ocr=true` salvo que OCR genérico sea claramente preferible.
7. Extraer páginas confirmadas a PDF separado.
8. Generar un PDF pre-OCR donde las páginas confirmadas son sustituidas por páginas textuales estándar con el análisis visual. Páginas candidatas no confirmadas, como tablas grandes, quedan intactas.

**Outputs**:
- `/proyecto/artifacts/step_1_planos/{stem}_page_size_audit.json`
- `/proyecto/artifacts/step_1_planos/{stem}_candidate_pages/page_XXXX.png` (temporales/auditables)
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.pdf`
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.json`
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.md`
- `/proyecto/artifacts/step_1_pdfs_preocr/{stem}_preocr.pdf`

**Reglas**:
- No borrar nunca páginas del PDF limpio; solo crear derivados.
- No excluir por tamaño únicamente: requiere confirmación visual.
- No inventar cantidades ni códigos no legibles.
- Si una página grande es tabla/anexo textual, dejarla en el flujo OCR normal (`exclude_from_ocr=false`).
- LandingAI/OCR debe consumir `{stem}_preocr.pdf` si existe; si no existe, consumir `{stem}_clean.pdf`.

### 1.3 OCR + parsing a Markdown

**Owner**: Orquestador (script `scripts/extractors/landingai_extract.py`)
**Tarea**: pasar cada PDF optimizado por LandingAI ADE → Markdown con tablas preservadas + separadores `<!-- PAGE n -->`.

**Output**: `/proyecto/artifacts/step_1_normalizados/{nombre_doc}.md`
**Gate 1**: Pausar. Presentar markdowns al humano para spot-check rápido en documentos críticos (EETT principales).
**Done**: todos los inputs tienen su `.md`.

**Fallback** (si LandingAI deja gaps en alguna página): LLM call one-shot con visión sobre la página específica (Gemini 2.5 Pro o GPT-5.5, ver `model_routing.yaml → paso_1_vision_fallback`).

---

## Paso 1.4 — Incorporar aclaraciones → documentos "aclarados"

**Tipo**: evaluator-optimizer bounded (ver agent_patterns §2.2).

### 1.4.1 Ejecutor

**Owner**: Orquestador → 1 subagente
**Tipo**: LLM call con tools `file` + `terminal`
**Prompt**: `prompts/prompt_merge_aclaraciones_ejecutor.md`
**Modelo**: desde `model_routing.yaml → paso_1_4_ejecutor` (primary: glm-5p1)
**Context** (paths, NO contenido):
- Rutas a EETT/anexos en MD (de step_1_normalizados)
- Rutas a aclaraciones en MD
- Schema de salida: `schemas/...`
- Output path: `/proyecto/artifacts/step_1_aclaradas/{nombre_doc}_aclarada_v1.md`
**Tool budget**: `model_routing.yaml → paso_1_4_ejecutor.tool_budget`

**Reglas**:
- Edición quirúrgica (patches con marca de trazabilidad), NO re-escritura del documento completo.
- Marcas obligatorias: `[Modificado según Aclaración X, punto Y]`, `[Agregado según...]`, `[Eliminado según...]`.

### 1.4.2 Auditor

**Owner**: Orquestador → 1 subagente
**Modelo**: DISTINTO al ejecutor (`model_routing.yaml → paso_1_4_auditor`, primary: minimax-m2p7).
**Prompt**: `prompts/prompt_merge_aclaraciones_auditor.md`
**Handoff budget**: 1 (auditor revisa una vez, sin reverse edge).

**Reglas**:
- Si auditor encuentra cobertura 1:1 con marcas correctas → OK.
- Si encuentra gaps mayores → **falla loud y escala al humano**, NO devuelve al ejecutor para "otra iteración".

**Gate 2**: presentar reporte de auditoría al humano. Esperar aprobación.
**Done**: cobertura 1:1 + aprobación humana.

> **Regla post-1.4**: a partir de aquí, SOLO se usan los documentos "aclarados".

---


## Paso 1.5 — Índice estructural y sugerencias de reparación Markdown

> **Nuevo en v0.3 experimental**: antes de extraer BOM, reconstruir la estructura real de cada documento Markdown mediante lectura completa con overlap. No confiar ciegamente en headings Markdown generados por extractores.

**Tipo**: LLM call / workflow de indexación, una llamada por documento.
**Owner**: Orquestador → 1 subagente por documento Markdown.
**Prompt**: `prompts/prompt_document_indexer.md`
**Schema**: `schemas/document_index.schema.json`
**Modelo**: `model_routing.yaml → paso_1_5_indexador`

**Inputs** (paths, NO contenido):
- Documento Markdown fuente:
  - si Paso 1.4 aplica: `/proyecto/artifacts/step_1_aclaradas/{stem}_aclarada_v1.md`
  - si no hay aclaraciones: `/proyecto/artifacts/step_1_normalizados/{stem}.md`
- Schema: `instrucciones/schemas/document_index.schema.json`
- Prompt: `instrucciones/prompts/prompt_document_indexer.md`

**Outputs** (sin subcarpetas por documento):
- `/proyecto/artifacts/step_1_index/{stem_original}_index.json`
- `/proyecto/artifacts/step_1_index/{stem_original}_index.md`

**Método obligatorio**:
- Leer TODO el Markdown de principio a fin.
- Ventanas fijas de 200 líneas.
- Overlap fijo de 50 líneas.
- Ejemplo: `1-200`, `151-350`, `301-500`, etc.
- La pasada NO extrae BOM, NO extrae entregables y NO reconcilia fuentes.
- Reconstruir jerarquía usando señales combinadas: numeración, títulos, tabla de contenido, `CAPITULO`, `FORMATO`, `ANEXO`, `Cláusula`, `Partida`, continuidad temática y cambios de formato.

**Reglas sobre Markdown defectuoso**:
- El indexador puede detectar errores estructurales del Markdown, pero NO modifica el Markdown fuente.
- Debe registrar sugerencias de bajo riesgo en `markdown_corrections_suggested`, por ejemplo:
  - heading falso dentro de lista: `## d)` → `d)`;
  - lista numerada/alfabética rota;
  - heading partido;
  - heading pegado al cuerpo;
  - header de tabla repetido;
  - ruido OCR de salto de línea.
- No se permiten reescrituras semánticas, completado de contenido faltante ni “mejoras” de redacción.
- Si una corrección no es claramente segura, `safe_auto_apply=false`.

**Validación**:
- JSON debe parsear con `json.load`.
- Validar contra schema cuando `jsonschema` esté disponible.
- Segundo fallo de schema tras retry = falla loud.

### Paso 1.5b — Reparación Markdown opcional y reversible

**Tipo**: workflow determinístico opcional.
**Owner**: Orquestador.
**Input**: `{stem_original}_index.json` con `markdown_corrections_suggested`.
**Tarea**: aplicar únicamente correcciones con `safe_auto_apply=true` y `confidence=high` a una copia del Markdown, nunca al archivo fuente.

**Outputs**:
- `/proyecto/artifacts/step_1_repaired/{stem_original}_repaired.md`
- `/proyecto/artifacts/step_1_repaired/{stem_original}_repair.patch`
- `/proyecto/artifacts/step_1_repaired/{stem_original}_repair_log.json`

**Reglas**:
- Mantener original intacto.
- Cambio reversible y auditable.
- Si hay dudas, no aplicar: dejar como sugerencia para revisión humana.

**Uso downstream**:
- Paso 2 y Paso 3 deben preferir índices estructurales y rangos/section_ids para seleccionar contexto.
- Si existe Markdown reparado y fue aprobado, usarlo como fuente textual; si no, usar el normalizado/aclarado original.

---
## Paso 2A — Lectura temática lineal por documento — **v0.3**

> Giro de diseño: antes de construir BOM consolidado, lanzar lectores especializados por eje y documento. Cada subagente produce **solo JSON canónico**; el orquestador valida y renderiza Markdown determinísticamente.

> Ajuste híbrido: no todos los ejes deben usar el mismo nivel de rigidez en la primera lectura. Para ejes de comprensión global, especialmente eje 0, se permite una lectura libre/semiestructurada antes de canonicalizar.

### 2A.axis0 — Lectura libre híbrida para datos principales

**Uso recomendado**: eje 0 cuando el expediente completo o sus documentos principales caben razonablemente en contexto largo o pueden leerse por offsets sin análisis por chunk.

**Patrón**:
1. Lanzar 2 lectores libres con modelos contrastantes.
2. El prompt de lectura debe ir **inline** en el handoff del subagente; no pedir “lee este prompt desde una ruta” salvo que sea demasiado largo.
3. Pasar la ruta de la **carpeta de documentos fuente del expediente** y, si existe, un inventario breve de documentos normalizados. El lector debe revisar/buscar en todos los documentos relevantes. Solo pasar un documento específico si el orquestador limita explícitamente el alcance.
4. Si el tool de lectura trunca, los offsets son solo transporte, no chunking semántico.
5. El lector produce Markdown claro/semiestructurado, no JSON canónico.
6. El orquestador compara outputs, consolida, detecta discrepancias y verifica personalmente contra los MD/PDF fuente.
7. El JSON/schema canónico se produce después, como capa de normalización, no como restricción de primera lectura.

**Prompt base**: `prompts/prompt_axis0_free_reader.md` (contenido suficientemente corto para pegar inline).

**La lectura libre debe pedir explícitamente**:
- cronograma;
- objeto/alcance con hasta 8 familias principales de bienes/equipos;
- clasificación contractual;
- presupuesto;
- plazos/hitos/duraciones;
- requisitos del postor;
- garantías;
- pagos;
- penalidades;
- personal/perfiles;
- experiencia;
- evaluación/buena pro;
- entidades externas/supervisoras en aceptación/conformidad/reconocimiento/pago;
- condiciones contractuales principales;
- dudas o ambigüedades.

**Regla de alcance multi-documento**:
- Clientes privados (AAP, AdP, LAP, etc.): típicamente hay bases + uno o más documentos técnicos/anexos; leer todos.
- Estado peruano: puede ser un documento consolidado o principal + anexos; leer principal y anexos relevantes.
- OACI/ICAO, BID u organismos multilaterales: la información suele estar dispersa entre varios documentos; leer/buscar en todo el paquete.

### 2A.0 Construir chunk plans determinísticos

**Owner**: Orquestador / script determinístico.
**Script**: `scripts/build_section_chunks.py`
**Inputs**:
- Markdown normalizado del documento.
- Índice estructural Paso 1.5 `{stem}_index.json`.
**Output**:
- `/proyecto/artifacts/step_2_chunks/{stem}_chunks.json`

**Reglas**:
- Target aproximado: 500 líneas.
- Cortar preferentemente en boundaries de sección/numeral del índice.
- Absorber gaps pequeños para cobertura total.
- El subagente debe seguir `chunks[]` en orden; no inventa ventanas propias.

### 2A.1 Lectores temáticos JSON-only

**Owner**: Orquestador → subagentes especializados.
**Prompt template**: `prompts/prompt_thematic_reader.template.md`
**Axis payloads**: `payloads/thematic_axes/{axis_id}.json`
**Rendered prompts**: `prompts/rendered_thematic/{axis_id}.md`
**Renderer**: `scripts/render_thematic_prompt.py`
**Schema base**: `schemas/thematic_extraction.schema.json` v0.3
**Schemas por eje**:
- `schemas/axis_0_main_tender_data.schema.json`
- `schemas/axis_1_proposal_signature_documents.schema.json`
- `schemas/axis_2_execution_documentary_deliverables.schema.json`
- `schemas/axis_3_execution_services_obligations.schema.json`
- `schemas/axis_4_goods_licenses_equipment.schema.json`

Usar el schema específico del eje al llamar subagentes; el schema base queda como shape común/referencia.

**Modelo piloto**: `openai/gpt-5.4-mini`.

**Inputs por subagente**:
- prompt renderizado del eje (`prompts/rendered_thematic/{axis_id}.md`)
- `source_md_path`
- `document_index_path`
- `chunk_plan_path`
- `axis_id` / `axis_name`
- `axis_payload_path`
- `schema_path` específico del eje
- `output_json_path`

**Output del subagente**:
- Solo JSON canónico en `output_json_path`.
- No escribir Markdown ni otros derivados.

**Ejes**:
0. Datos principales comerciales/contractuales.
1. Documentos de propuesta y firma/formalización.
2. Entregables documentales de ejecución.
3. Servicios/obligaciones de ejecución y post-entrega.
4. Bienes, licencias y equipamiento (BOM-HL como menciones evidenciadas, no consolidado final).

**Campos clave**:
- líneas fuente y `evidence_excerpt` obligatorio (cita corta <=400 chars);
- `mention_type`: explicit/implied;
- `phase`;
- `source_context_type`;
- `is_primary_requirement`;
- `conditional_applicability`;
- `dedupe_context` / `cross_axis_notes`.

Para eje 4, además:
- `item_name` / `item_family` libres para dedupe/búsqueda;
- `supply_model`: COTS, custom-made, configured COTS, mixed, unclear;
- `custom_spec_relevance`: si la verificación debe mirar ficha técnica, specs de proyecto, ambos, etc.

### 2A.2 Validación y Markdown derivado

**Owner**: Orquestador / scripts determinísticos.
**Scripts**:
- `scripts/validate_thematic_extraction.py`
- `scripts/render_thematic_md.py`

**Validación mínima**:
- JSON parse + schema.
- `evidence_excerpt` presente y <=400 chars.
- rangos de líneas válidos y dentro del Markdown fuente.
- cobertura reportada contiene los chunks del `chunk_plan_path`.

**Derivado humano**:
- El Markdown `{document}_{axis}.md` se genera desde JSON validado.
- Si cambia el formato de revisión, se regenera Markdown sin relanzar LLM.

### 2A.3 Consolidación posterior

No deduplicar dentro de los lectores. La consolidación/clustering se hace después, preservando `source_mentions[]` y evitando fusionar homónimos si difieren documento, fase, sistema, partida, aeropuerto, propósito o contexto.

---

## Paso 2B — Extracción de BOM consolidado (posterior a ledgers temáticos) — **pendiente de rediseño**

> El diseño previo de BOM HL/Exploded queda subordinado a Paso 2A. Usar ledgers temáticos como evidencia antes de consolidar.

### 2.0 Inicializar scratchpad compartido

**Owner**: Orquestador (determinístico)
**Tarea**: crear `/proyecto/scratchpad/decisiones_bom.md` vacío con headers:
- Convenciones de naming (cómo nombrar grupos, sub-sistemas, ítems compuestos)
- Abreviaturas usadas (ej. `VSAT`, `HUB`, `UPS`)
- Supuestos técnicos (ej. "los cables de baja potencia ≤24V se asocian al equipo destino, no a la fuente")

Este archivo se enriquece a medida que el productor toma decisiones. El auditor y los siguientes pasos lo leen.

### 2.1 BOM High-Level — Productor

**Tipo**: LLM call con schema fuerte.
**Owner**: Orquestador → 1 subagente
**Prompt**: `prompts/prompt_bom_highlevel.md`
**Modelo**: `model_routing.yaml → paso_2_1_productor` (primary: glm-5p1).
**Context** (paths):
- Rutas a EETT aclaradas + anexos aclarados
- Schema: `schemas/bom_item.schema.json`
- Scratchpad: `/proyecto/scratchpad/decisiones_bom.md` (lee y escribe decisiones nuevas)
- Output path: `/proyecto/artifacts/step_2_bom/BOM_highlevel.json`
**Tool budget**: `max_file_reads: 15`, `max_file_writes: 3`.
**Output validation**: schema_validation strict — si JSON inválido tras 1 retry, falla loud.

### 2.2 BOM High-Level — Auditor "ojos frescos"

**Tipo**: LLM call.
**Owner**: Orquestador → 1 subagente (modelo DISTINTO).
**Prompt**: `prompts/prompt_bom_auditor.md` (nuevo en v0.2)
**Modelo**: `model_routing.yaml → paso_2_1_auditor` (primary: Kimi-K2.6).
**Context**: BOM HL JSON + EETT aclaradas + scratchpad.
**Handoff budget**: 1.

**Tarea del auditor**:
- Verificar completitud (items faltantes).
- Verificar consistencia con scratchpad (naming, agrupación).
- Reportar items faltantes / items sobrantes / clasificaciones inconsistentes.

**Resolución**:
- Si auditor reporta solo problemas menores: el orquestador aplica correcciones determinísticas (renames, reagrupaciones según scratchpad).
- Si auditor reporta items faltantes mayores: **falla loud y escala al humano**.

**Output final**: `/proyecto/artifacts/step_2_bom/BOM_highlevel.json` (corregido si aplica)
**Done**: BOM HL auditado, scratchpad actualizado.

### 2.3 BOM Exploded — Productor

**Tipo**: LLM call con schema fuerte.
**Owner**: Orquestador → 1 subagente
**Prompt**: `prompts/prompt_bom_exploded.md`
**Modelo**: `model_routing.yaml → paso_2_3_productor` (primary: glm-5p1).
**Context** (paths):
- BOM HL auditado (de 2.2)
- EETT aclaradas (selección por sección, no documento completo — context engineering "select")
- Scratchpad actualizado
- Schema: `schemas/bom_item.schema.json`
- Output path: `/proyecto/artifacts/step_2_bom/BOM_exploded.json`
**Tool budget**: `max_file_reads: 30`, `max_file_writes: 3`.
**Output validation**: schema_validation strict.

**Estrategia de context**:
- Si el BOM HL tiene >20 items, procesar en **batches por grupo** (Comunicaciones / Energía / Infraestructura / etc.), cada batch con context selectivo de las secciones EETT relevantes.
- Cada batch escribe a un sub-archivo, el orquestador concatena al final.

### 2.4 BOM Exploded — Auditor "ojos frescos"

**Tipo**: LLM call.
**Owner**: Orquestador → 1 subagente (modelo DISTINTO).
**Prompt**: `prompts/prompt_bom_auditor.md`
**Modelo**: `model_routing.yaml → paso_2_3_auditor` (primary: minimax-m2p7).
**Handoff budget**: 1.

**Tarea del auditor**:
- Verificar desagregación completa (cables, conectores, fuentes, kits separados).
- Verificar `parent_id` correcto en cada accesorio.
- Verificar que `requisitos_en_contexto` esté presente y verbatim.
- Reportar gaps.

**Resolución**:
- Problemas menores: orquestador aplica correcciones determinísticas.
- Problemas mayores: falla loud → humano.

**Output final**: `/proyecto/artifacts/step_2_bom/BOM_exploded.json`
**Done**: BOM Exploded auditado, scratchpad finalizado.

### 2.5 Item Pack — Generar un archivo JSON+MD por ítem (determinístico)

**Owner**: Orquestador (script determinístico, 1 pasada)
**Inputs**: `BOM_exploded.json`
**Outputs**:
- Canónicos: `/proyecto/artifacts/step_2_5_items/ITEM-{id}.json`
- Derivados (auto): `ITEM-{id}.md`
**Done**: 1 JSON+MD por ítem.

---

## Paso 3 — Verificación, refinamiento y herencia de specs

> **Cambio v0.2**: orden topológico explícito (padres antes que hijos) + batches de 5 items con context selectivo.

### 3.1 Verificar y completar specs por ítem

**Tipo**: LLM call en batches.
**Owner**: Orquestador → lanza N batches secuencialmente (no paralelo, para mantener orden topológico).

**Orden de procesamiento**:
1. Identificar capas topológicas por `parent_id`:
   - Capa 0: items sin parent.
   - Capa 1: items con parent en capa 0.
   - Capa 2: etc.
2. Procesar capa 0 completa antes de capa 1, etc. Esto asegura que cuando un hijo necesita resolver herencia, su padre ya está procesado.

**Prompt**: `prompts/prompt_specs_verificacion_herencia.md`
**Modelo**: `model_routing.yaml → paso_3_1_productor` (primary: glm-5p1).
**Batching**: 5 items por batch (de `model_routing.yaml`).
**Context** (paths):
- Items del batch: `ITEM-{id}.json` (con requisitos_en_contexto del paso 2)
- EETT aclaradas: solo las secciones referenciadas por los items del batch (selective context)
- Specs de padres ya procesados (si los items del batch tienen parent_id)
- Schema: `schemas/item_specs.schema.json`

**Output validation**: schema_validation strict por batch. Si un batch falla, retry una vez; segundo fallo = falla loud para ese batch.

**Outputs**: `/proyecto/artifacts/step_3_specs/ITEM-{id}_specs.json` + `.md`
**Done**: todos los items procesados con `estado_specs: VERIFICADO`.

### 3.2 Revisión "ojos frescos"

**Tipo**: LLM call.
**Owner**: Orquestador → 1 subagente.
**Prompt**: `prompts/prompt_specs_revisor.md`
**Modelo**: DISTINTO al de 3.1 (`model_routing.yaml → paso_3_2_revisor`, primary: minimax-m2p7).
**Context** (paths):
- Todos los `ITEM-{id}_specs.json` de 3.1
- EETT aclaradas

**Tarea**: verificar completitud, herencia, clasificación hard/soft, trazabilidad, coherencia ítem↔requisitos.

**Estrategia para evitar context overload**: procesar en chunks de 15 items, generar reporte parcial por chunk, concatenar reportes al final.

**Output**: `/proyecto/artifacts/step_3_specs/revision_specs.md`
**Handoff budget**: 1 — si reporta problemas críticos, falla loud → humano.
**Done**: revisión OK o correcciones aplicadas.

---

## Paso 4 — BOM "para búsqueda" (determinístico)

**Tipo**: workflow no-LLM.
**Owner**: Orquestador (script `proyecto/scripts/step4_bom_busqueda.py`).
**Inputs**: `BOM_exploded.json` + `ITEM-{id}_specs.json` (todos).
**Tarea**:
- Filtrar bienes (descartar `tipo: SERVICIO`).
- Quitar cantidades.
- Limpiar referencias no-buscables (normas/autoridades locales → estándares internacionales si discernibles; si no, marcar para revisión).

**Output**: `/proyecto/artifacts/step_4_busqueda/BOM_busqueda.json` (+ `.tsv` derivado).
**QA interno**: sin servicios, sin cantidades, sin refs no-buscables, completitud.
**Done**: BOM búsqueda limpio.

---

## Paso 5 — REMOVIDO en v0.2

> En v0.1 era "confirmar preferencias" entre Paso 4 y Paso 6. Se elimina: si las preferencias cambian, se actualiza `overlay_usuario.yaml` y se relanza Paso 6 selectivamente. Innecesario como gate fijo.

Una nueva pausa **opcional** post-Paso 4 permite al humano marcar items como `SKIP` o `DIFERIR` antes de invertir tokens en Paso 6 (de `MEJORAS_PROPUESTAS.md` mejora 5.1).

---

## Paso 6 — Búsqueda de candidatos (ÚNICO paso multi-agent del workflow)

> **Tipo**: orchestrator-workers con fan-out paralelo (ver agent_patterns §2.3).
> **Cambio v0.2**: matriz de cumplimiento como LLM call SEPARADA post-búsqueda + búsqueda bilingüe + tool budget explícito + pool de tools con fallback.

### Arquitectura

```
Orquestador
  → Subagente-Item (1 por ítem, en batches de N items simultáneos)
      → Search-Worker A (modelo + Brave + EN priority)
      → Search-Worker B (modelo + Exa + ES priority)
      [Item-manager valida candidatos]
      [Item-manager invoca Generador-Matriz para cada candidato Válido/Condicionado]
```

### 6.1 Lanzar Subagentes-Item (en batches)

**Owner**: Orquestador.
**Prompt**: `prompts/prompt_item_manager.md`
**Modelo**: `model_routing.yaml → paso_6_item_manager` (primary: glm-5p1).
**Context** (paths):
- `ITEM-{id}_specs.json`
- `/proyecto/overlay_usuario.yaml`
- `catalog_tools.md`
- `formato_matriz_cumplimiento.md`
- Schema candidato: `schemas/candidato_cumplimiento.schema.json`
**Tool budget**: `model_routing.yaml → paso_6_item_manager.tool_budget` (12 search calls + 16 fetch + 6 PDF parses + 3 iteraciones max).
**Timeout**: `params.yaml → timeouts.item_manager` (1500s).
**Batch size**: `params.yaml → batching.batch_size_items_step6` (3 items simultáneos).

### 6.2 Lógica interna del Subagente-Item

(Se define en detalle en `prompt_item_manager.md`. Resumen:)

1. **Fan-out a 2 search-workers en paralelo**:
   - Worker A: modelo `paso_6_search_worker_a` + tool `brave` + idioma EN priority.
   - Worker B: modelo `paso_6_search_worker_b` + tool `exa` + idioma ES priority.
   - Cada worker recibe su `tool_budget` y devuelve candidatos en JSON estructurado (no texto libre).

2. **Consolidación de candidatos** (determinístico): deduplicar por marca+modelo+PN.

3. **Validación por candidato**:
   - Vigencia (página fabricante activa, no EOL).
   - Origen/marca según overlay.
   - Capacidad de descargar datasheet PDF del fabricante (ver `catalog_tools.md` capa C).
   - Verificación de requisitos Hard contra datasheet.
   - Clasificación: VALIDO | CONDICIONADO | DESCARTADO.

4. **Generación de matriz de cumplimiento** (LLM call SEPARADA, ver 6.3): por cada candidato VALIDO/CONDICIONADO.

5. **Decisión de relanzar**:
   - Si ≥1 VALIDO y queda iteración: intentar completar 3 candidatos.
   - Si 0 VALIDO y queda iteración: relanzar con exclusiones dinámicas (modelos descartados) y combos rotados.
   - Si agotó iteraciones sin VALIDO: estado `SIN_CANDIDATO` + diagnóstico.

### 6.3 Generación de matriz de cumplimiento (LLM call separada)

**Tipo**: LLM call invocada por el Subagente-Item para cada candidato Válido/Condicionado.
**Prompt**: `prompts/prompt_matriz_cumplimiento.md` (nuevo en v0.2)
**Modelo**: `model_routing.yaml → paso_6_matriz_cumplimiento` (primary: glm-5p1).
**Context** (paths):
- `ITEM-{id}_specs.json` (requisitos verbatim)
- Path al datasheet PDF descargado + parseado del candidato
- Schema: `schemas/candidato_cumplimiento.schema.json`
**Output**: `/proyecto/artifacts/step_6_resultados/matrices/ITEM-{id}/ITEM-{id}_candidato_{n}_{marca}_{modelo}.json` + `.md`.
**Handoff budget**: 1 (sin reverse edge).

### 6.4 Recepción y Gate

**Outputs**:
- `/proyecto/artifacts/step_6_resultados/items/ITEM-{id}_resultado.json` + `.md` (1 por ítem)
- `/proyecto/artifacts/step_6_resultados/matrices/ITEM-{id}/*.json` + `.md` (1 par por candidato Válido/Condicionado)

**Gate 4**: para items con estado SIN_CANDIDATO, pausar y presentar diagnóstico. El humano decide: relajar requisito / aceptar condicionado / búsqueda manual.

---

## Paso 7 — Consolidación final + QA

### 7.1 Producir consolidado

**Tipo**: LLM call con schema.
**Owner**: Orquestador → 1 subagente.
**Prompt**: `prompts/prompt_consolidacion_paso7.md`
**Modelo**: `model_routing.yaml → paso_7_1_consolidador` (primary: glm-5p1).
**Context** (paths):
- `BOM_busqueda.json`
- `BOM_exploded.json` (para incluir servicios en consolidado)
- Todos los `ITEM-{id}_resultado.json`
- Todas las matrices JSON
- Schema: `schemas/consolidado_row.schema.json`

**Output canónico**: `/proyecto/outputs/consolidado.json`
**Derivados (auto, determinísticos)**: `.tsv`, `.md`, `.xlsx`.

### 7.2 QA Final

**Tipo**: LLM call (critic terminal).
**Owner**: Orquestador → 1 subagente.
**Prompt**: `prompts/prompt_QA_final.md`
**Modelo**: DISTINTO al consolidador (`model_routing.yaml → paso_7_2_qa`, primary: minimax-m2p7).
**Handoff budget**: 0 (sin reverse edge). Si QA encuentra problemas críticos: **falla loud y escala**, NO retorna al consolidador.

**Output**: `/proyecto/outputs/QA_report.md`
**Done**: QA OK o problemas escalados al humano.

---

## Resumen de artefactos preservados

```
proyecto/
├── inputs/                                (fuentes originales, read-only)
├── overlay_usuario.yaml                   (preferencias)
├── scratchpad/
│   └── decisiones_bom.md                  (decisiones compartidas entre 2.1-2.4)
├── artifacts/
│   ├── step_1_pdfs/                       (DOCX→PDF si aplica)
│   ├── step_1_pdfs_clean/                 (post optimizer; PDFs nombrados `{stem}_clean.pdf`)
│   ├── step_1_planos/                     (planos detectados, análisis visual, páginas extraídas)
│   ├── step_1_pdfs_preocr/                (PDFs con planos sustituidos por resumen textual)
│   ├── step_1_normalizados/               (markdowns post-LandingAI/preocr)
│   ├── step_1_aclaradas/                  (docs aclarados + auditoría)
│   ├── step_1_index/                      (`{stem}_index.json/.md`; índice estructural + correcciones Markdown sugeridas)
│   ├── step_1_repaired/                   (opcional; Markdown reparado, patch y log)
│   ├── step_2_bom/                        (BOM HL y Exploded en JSON + derivados)
│   ├── step_2_5_items/                    (1 JSON+MD por ítem)
│   ├── step_3_specs/                      (ítems verificados + revisión)
│   ├── step_4_busqueda/                   (BOM búsqueda)
│   └── step_6_resultados/
│       ├── items/                         (resultado por ítem)
│       └── matrices/ITEM-XXX/             (matrices por candidato)
├── outputs/
│   ├── consolidado.json / .tsv / .md / .xlsx
│   └── QA_report.md
└── logs/
    └── decision_log.md
```

---

## Auditoría y trazabilidad

Cada delegación se logea en `/proyecto/logs/decision_log.md` con:
- Timestamp.
- Paso + subpaso.
- Modelo elegido (y motivo si difiere de `model_routing.yaml`).
- Tools usadas (y motivo de fallback si aplica).
- Tool budget consumido (búsquedas / fetches / parses / iteraciones).
- Handoff budget consumido.
- Duración.
- Errores y retries.
- Escalamientos al humano.

Tras cada licitación: producir `autoevaluacion_v{N}.md` con incidentes en formato post-mortem honesto (sin auto-justificación). Actualizar `model_routing.yaml → observations` y `catalog_tools.md` con aprendizajes.
