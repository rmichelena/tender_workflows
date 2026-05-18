# Roadmap / Observaciones sueltas

Notas vivas del workflow `tender_procurement`. Este archivo captura hallazgos que no ameritan implementación inmediata, decisiones de diseño y mejoras candidatas.

## Paso 1.2b — Planos / diagramas pre-OCR

### Texto de reemplazo para imágenes

- Las regiones `replace_images` deben reemplazarse como **imagen renderizada** dentro del rectángulo real de la imagen original, no como overlay de texto PDF.
- El rectángulo usado para redaction/replacement debe venir del **rendered final rect** de PyMuPDF (`page.get_text("dict")`, blocks `type=1`, o equivalente), no del bbox aproximado del modelo visual.
- El `bbox_pct` del modelo visual debe tratarse como locator aproximado, nunca como geometría destructiva final.
- Evitar márgenes arbitrarios en redactions: pueden borrar texto real alrededor.
- Guardar PDFs generados con compresión/garbage collection (`garbage=4`, `deflate=True`, `deflate_images=True`, `deflate_fonts=True`, `clean=True`).

### Formato OCR-friendly del texto renderizado

Objetivo: que Docling/OCR lea el bloque como texto plano descriptivo, sin promoverlo a headings Markdown ni introducir jerarquía falsa.

Preferir:

```text
[imagen reemplazada] ver planos_extraidos
[resumen] texto corto...
[texto visible] códigos principales...
[notas] observaciones técnicas...
```

Evitar:

```text
4.12 Topología del aeropuerto...
Códigos/texto visible:
Información técnica visible:
```

Motivo: líneas que arrancan con numeración formal (`4.12`, `Partida 3.00`) o parecen títulos pueden ser promovidas por Docling a `##`/`###` aunque solo sean metadata de sustitución visual.

## Paso 1.5 — Índice estructural

### Correcciones Markdown sugeridas

Las correcciones sugeridas por el indexador son útiles como QA, pero no todas deben aplicarse automáticamente.

Aplicación automática solo cuando:

- el patrón sea determinístico;
- conserve texto exactamente;
- sea puramente estructural;
- no cambie numeración oficial ni interpretación legal/técnica.

Ejemplos candidatos a auto-apply:

- bajar `##` a `###` cuando claramente son subtítulos internos promovidos por Docling;
- consolidar heading duplicado consecutivo que refiere a la misma sección;
- eliminar/reducir headers de tabla repetidos cuando el patrón exacto es verificable.

No auto-apply:

- renumeraciones de secciones (`1.1 → 7.1`, `8.1 → 9.1`);
- cambios que podrían corregir un error del documento original, no del extractor;
- reparaciones semánticas o inferidas.

### Campos de schema considerados

No agregar `page_start/page_end` inferidos por LLM. Si el Markdown no conserva marcas de página, el modelo no puede saber páginas de forma confiable.

Si se necesitan páginas, deben venir de upstream:

- Docling con page markers reales;
- inyección determinística de `<!-- page: N -->` durante parsing;
- mapa externo `line_range → page_number`.

Campos que sí podrían agregarse después:

- `source_line_start` / `source_line_end`: verificables desde Markdown.
- `duplicate_of_section_id`: útil para contenido repetido entre Bases/Contrato o tablas duplicadas.
- `markdown_artifact_type`: para clasificar artifacts de Docling, OCR o reemplazos visuales.
- `repair_confidence` / `repair_scope`: separar reparación determinística segura de inferencia semántica.
- `source_page_start` / `source_page_end`: solo si los provee el pipeline, no si los infiere el LLM.

### Paso 1.5 — Evitar extracción factual accidental

Hallazgo: el resumen Markdown del índice puede tender a incluir datos factuales (montos, plazos, requisitos de consorcio, cantidades) porque el schema tiene campos `summary`/`notes` y el prompt pide un resumen humano.

Decisión:

- El índice estructural no debe convertirse en mini-resumen del documento.
- `summary` debe describir la función estructural de la sección, no sus datos.
- Evitar valores específicos salvo que sean necesarios para distinguir una sección/tabla.

Ejemplo preferido:

```text
8. Monto Máximo — sección de presupuesto/monto referencial
2. Condiciones de Consorcios — sección de requisitos de participación en consorcio
```

Evitar:

```text
8. Monto Máximo — US$ 1,327,351.76 incl. IGV
2. Condiciones de Consorcios — máximo 2 integrantes, mínimo 40% participación
```

Si después se necesita extracción factual, debe ser otro paso con schema propio y control de evidencia.

## Paso 2A — Chunk plan determinístico

Decisión: los subagentes temáticos no deben inventar ventanas de lectura. El orquestador precomputa un `{basename}_chunks.json` por documento a partir del índice estructural de Paso 1.5.

Reglas:

- Target aproximado: 500 líneas.
- Cortar preferentemente en boundaries de sección/numeral del índice.
- Si una sección grande no tiene subdivisión fina, se conserva como chunk grande antes que cortar arbitrariamente.
- Gaps pequeños del índice se absorben en chunks vecinos para garantizar cobertura total del Markdown.
- El subagente recibe `chunk_plan_path` y debe leer `chunks[]` en orden.

Campos útiles del chunk plan:

- `chunk_id`
- `line_start` / `line_end`
- `section_ids`
- `section_titles`
- `split_from_section_id` / `split_part` si hubo split mecánico
- `coverage.missing_ranges` debe quedar vacío para documentos listos para Paso 2A.

## Paso 2A — JSON canónico, Markdown derivado

Decisión: los subagentes temáticos producen solo JSON validado. El orquestador genera Markdown con `scripts/render_thematic_md.py`.

Motivos:

- Menos carga cognitiva para el subagente.
- Formato humano consistente entre ejes/documentos.
- Las citas textuales (`evidence_excerpt`) se presentan siempre de la misma manera.
- Cambios de formato Markdown no requieren relanzar LLM.
- QA más simple: si el JSON cumple schema y checks adicionales, el derivado es mecánico.

Checks mínimos con `scripts/validate_thematic_extraction.py`:

- schema v0.3;
- `evidence_excerpt` obligatorio y <=400 caracteres;
- líneas válidas contra Markdown fuente;
- cobertura reportada compatible con `chunk_plan_path`;
- `entry_id` único.

## Paso 2A — Schemas específicos por eje

Decisión: el schema genérico `thematic_extraction.schema.json` es útil como base común, pero no debe ser el contrato directo para subagentes cuando el eje es semánticamente estrecho.

Problema observado:

- En eje 4, modelos podían usar `phase=proposal` o `entry_type` libre/incorrecto aunque los bienes no se entregan en fase de propuesta.
- El schema genérico permitía `source_context_type=other` y `entry_type` arbitrario, abriendo la puerta a sobre-inclusión.

Solución:

- Mantener `thematic_extraction.schema.json` como shape común.
- Usar schemas especializados por eje con enums cerrados:
  - `axis_0_main_tender_data.schema.json`
  - `axis_1_proposal_signature_documents.schema.json`
  - `axis_2_execution_documentary_deliverables.schema.json`
  - `axis_3_execution_services_obligations.schema.json`
  - `axis_4_goods_licenses_equipment.schema.json`

Regla importante para eje 4:

- `phase` se refiere a cuándo se entrega/usa el bien, no dónde aparece mencionado.
- Por tanto, `phase=proposal` no está permitido para bienes/licencias/equipamiento.
- Si el bien aparece en un formato/presupuesto de propuesta, eso va en `source_context_type`, no en `phase`.

### Eje 4 — COTS vs custom-made

Decisión: no enumerar familias específicas de equipos en el schema. Hacerlo obligaría a crear un schema diferente por licitación y sería frágil.

`entry_type` debe ser una categoría genérica de procurement:

- `equipment`
- `software_license`
- `hardware_license`
- `material`
- `accessory`
- `spare_part`
- `consumable`
- `civil_works_material`
- `secondary_evidence_group`
- `other_good_component`

Para dedupe/búsqueda, usar campos libres:

- `item_name`: nombre normalizado del ítem.
- `item_family`: familia reusable libre, sin enum cerrado.

Agregar distinción de estrategia de suministro/verificación:

- `supply_model`: `cots`, `custom_made`, `configured_cots`, `mixed`, `unclear`.
- `custom_spec_relevance`: `datasheet_match_expected`, `project_specific_specs_matter`, `both`, `unclear`, `not_applicable`.

Ejemplo: un switch de red normalmente será `cots`/`datasheet_match_expected`; un carrusel de reclamo de equipaje puede ser `custom_made` o `mixed`, donde dimensiones/capacidad/layout del proyecto pesan más que una ficha técnica estándar.

## Paso 2A — Prompt template + payload por eje

Hallazgo: un único prompt `thematic_reader` genérico no da suficiente adherencia. Aunque el schema sea específico, el subagente necesita una identidad de rol y reglas de inclusión/exclusión propias del eje.

Decisión:

- Mantener un template común para inputs, método, output JSON-only, evidencia y validación.
- Construir la parte semántica desde payload JSON por eje.
- Renderizar prompts antes de llamar subagentes.

Payload por eje incluye:

- `role_frame`
- `task_axis_definition`
- `inclusion_rules`
- `exclusion_rules`
- `trigger_phrases`
- `axis_disambiguation_examples`
- `phase_rules`
- `axis_specific_fields`

Esto evita divergencia accidental en el contrato común, pero permite adherencia fuerte por tarea.

### Axis 0 schema refinement after prompt edits

Axis 0 now covers broader bid-decision data beyond the original generic commercial fields:

- tender classification (service/supply/supply+installation/support);
- delivery deadlines and milestones;
- advance/payment conditions;
- evaluation scoring factors;
- post-sale support, maintenance, warranty conditions;
- more precise source contexts for evaluation, qualification, payment, penalties, guarantees, experience, personnel, certifications and scope.

## 2026-05-18 — Gemini 2.5 Flash as subagent: failure analysis

Context: Roberto compared failed Gemini subagent runs for Paso 2A with a direct Discord channel test (`#prueba-gemini`). Findings:

- Gemini subagent invocation is reaching the intended provider/model (`google/gemini-2.5-flash`) and can issue tool calls, so this is not simply a model-routing failure.
- In failed subagent runs for eje 2:
  - Bases run read the rendered prompt, initially hallucinated a schema path missing `/workspace/`, corrected it, then the Google provider returned `unknown error` after tool results. The surfaced completion was the last schema/tool content, not a valid extraction artifact.
  - EETT run read prompt/schema/chunk plan/index, then provider returned `unknown error`; the surfaced output was the structural index/tool result, not an extraction.
- Direct Discord test in `#prueba-gemini` produced a plausible high-level extraction and wrote `artifacts/step_2_thematic/bases_axis_0_main_tender_data_gemini.json`, but the artifact was not valid JSON: parse error at line 215 due unescaped quotes. After one local repair, it still had 9 schema errors (long `evidence_excerpt`, null uncertainty line_start/line_end).
- Conclusion: Gemini Flash can produce plausible prose/extraction in a conversational flow, but is unreliable for the current strict JSON-only subagent contract with tool-heavy, schema-heavy prompts. Failures are both provider/tool-continuation issues and schema adherence issues.
- Recommended pattern if Gemini must be used again: keep the prompt much simpler, avoid giving it full schema/tooling responsibilities, make it produce draft content only, and have a deterministic or stronger-model post-processor validate/map to canonical JSON. Do not use Gemini Flash as primary producer for Paso 2A canonical artifacts.

## 2026-05-18 — Hybrid extraction strategy for axis 0

Observation from Roberto: for axis 0 (main tender/commercial/contractual data), the full documents are within long-context capacity (Bases ~120k tokens, EETT ~60k tokens). For this axis, forcing chunked line-by-line reading and strict canonical schema from the first pass may be unnecessary and can reduce quality.

Recommended hybrid pattern for axis 0:

1. **Free extraction readers**
   - Use 2 strong/contrasting models.
   - Give the full document when context allows; no chunking unless document exceeds context or provider limits.
   - Prompt with a complete checklist of information to extract (schedule, object, classification, budget, deadlines, eligibility, guarantees, payment, penalties, personnel, experience, evaluation, contract conditions).
   - Do not require line numbers in this first pass.
   - Allow natural prose / semi-structured output focused on recall and semantic understanding.

2. **Orchestrator consolidation**
   - Compare the two free outputs semantically.
   - Produce the canonical structured artifact.
   - Detect discrepancies (amounts, percentages, deadlines, guarantees, dates, counts).
   - Verify discrepancies directly against the source document before finalizing.

3. **Schema as downstream artifact, not first-pass constraint**
   - The schema remains useful for the final canonical output, but should not necessarily constrain first-pass comprehension.
   - This avoids making a single model act simultaneously as reader, legal analyst, schema mapper, JSON validator, line-citation engine, and file operator.

Expected benefits:
- Better recall for global/commercial facts.
- Fewer failures with models that reason well conversationally but struggle with strict tool/schema contracts.
- Discrepancy verification becomes explicit and auditable.

Caveat:
- Axes requiring exhaustive local traceability (e.g. axis 2 deliverables, axis 4 item lists) may still benefit from chunked/structured reading, especially when many small requirements appear throughout the document.

## 2026-05-18 — Axis 0 hybrid experiment results (Gemini Flash + DeepSeek V4 Flash)

Experiment: run axis 0 as free extraction over Bases, no schema, no chunk plan; then orchestrator consolidates and verifies discrepancies.

Artifacts:
- `artifacts/step_2_thematic/model_compare/free_axis0_bases_gemini25flash.md`
- `artifacts/step_2_thematic/model_compare/free_axis0_bases_deepseekv4flash.md`
- `artifacts/step_2_thematic/model_compare/free_axis0_bases_consolidated_orchestrator.md`

Findings:
- Gemini 2.5 Flash succeeded in free Markdown mode and produced a useful 21KB/424-line extraction. This contrasts with strict JSON-only subagent failures.
- DeepSeek V4 Flash required explicit offset reading because `read` truncates long files; after retry, it produced a useful ~20KB extraction.
- Gemini had strong global recall and clean organization.
- DeepSeek was more granular on contractual clauses/anomalies, but missed the base amount of the guarantee of faithful performance, claiming it was not found. Orchestrator verified line 1386: guarantee = 20% of winning economic proposal.
- Orchestrator verification resolved several discrepancy/doubt points:
  - final liquidation documentation = 10 calendar days (line 1094), not a live calendar/business-day contradiction;
  - manufacturing guarantee has two relevant anchors: 5 years from technical-operational conformity act per airport (line 109/1023), while contract term ties to OSITRAN recognition (line 1065);
  - support/maintenance guarantee says No aplica (lines 1408-1410), but post-sale/support obligations exist in manufacturing guarantee clauses (1177-1185);
  - similar goods definition is narrow: components corresponding to structured cabling (377-379);
  - penalty formula is genuinely malformed/ambiguous in MD line 1225 and should be checked in original PDF;
  - multiple template carryovers exist: Contrato de Obra, LPI-006-2025-AdP, LPN-001-2026-AdP, Contrato de Consultoría.

Conclusion:
- For axis 0, free dual-model extraction + orchestrator consolidation/verification is likely better than schema-first chunked extraction.
- Keep strict schema as final canonicalization layer, not as the first-pass reading constraint.

## 2026-05-18 — Axis 0 object/scope must include macro goods families

Refinement from Roberto: the axis 0 object/scope summary should not only say "supply of goods/equipment". It must name the major goods/equipment families included, at macro level, so a reader immediately understands what the procurement materially contains.

Rule added to `prompt_axis0_free_reader.md`:
- In the object/scope summary, include up to 8 major goods/equipment families.
- Choose the most important units for understanding the procurement.
- Avoid minor accessories unless they are a major unit of the requirement.
- Example granularity: switches core, switches de acceso/borde, access points, gateway de voz, telefonía IP, cableado estructurado/fibra óptica.

Applied to AdP axis 0 consolidated artifact:
- `free_axis0_bases_consolidated_orchestrator.md` now lists: switches core, switches de acceso/borde 24/48, access points, gateway de voz, telefonía IP, cableado estructurado voz/data with fiber-optic components.

## 2026-05-18 — Inline prompts for short/free-reader tasks

Refinement from Roberto: our prompts are short enough that passing only a prompt path adds an unnecessary meta-step. For subagents, especially Gemini-like models, “read the prompt from this path, then do the task” can dilute the force of the actual instruction.

Decision:
- For short prompts and free-reading tasks, paste the prompt inline in the subagent handoff.
- Keep file paths for versioning/reference, but do not make the model read a separate prompt file unless the prompt is too long or generated dynamically.
- Keep paths for large source documents, schemas, outputs, and artifacts.

Prompt cleanup:
- `prompt_axis0_free_reader.md` was simplified to remove anti-constraints like “do not use chunk plan / do not produce JSON / no schema / line numbers not required”. Those were distracting because a fresh subagent has no prior context of the old pattern.
- Axis 0 prompt now asks positively for tender analysis.
- Numeral 5 now asks generically for delivery/execution deadlines, differentiated milestones, and activities with specified durations.
- Numeral 8 removes explicit OSITRAN/MTC mention from payment.
- New requirement asks to identify external/supervisory entities involved in acceptance, conformity, recognition, approval, or payment.
- Numeral 13 removed confidentiality from the default “main conditions” checklist.

## 2026-05-18 — Free readers should receive the source document folder, not only Bases

Refinement from Roberto: axis 0 free readers should generally receive the folder/package of all source documents, not only the Bases document.

Rationale:
- Private airport clients (AAP, AdP, LAP, etc.) usually provide Bases plus one or more technical documents/anexos.
- Peruvian public-sector tenders may have one consolidated document or a main document plus annexes.
- ICAO/OACI, BID and multilateral tenders often distribute key information across several documents.

Decision:
- For free/hybrid reading, the normal input is the source-document folder (or normalized-doc folder) plus a brief inventory when available.
- The reader must search/read all relevant documents: bases, technical specs, TDR/EETT, annexes, forms, clarifications, etc.
- Passing only a single document is allowed only when the orchestrator explicitly scopes the run as an experiment or document-specific comparison.
- `prompt_axis0_free_reader.md`, `01_workflow.md`, and `README.md` were updated accordingly.

## 2026-05-19 — Workflow alignment: pre-OCR page analysis and search preferences gate

Roberto noted two workflow gaps:

1. Pre-OCR plan/diagram handling had been refined in scripts but not fully reflected in `01_workflow.md` / README.
2. The old Gate 0 asked for origin/brand preferences too early; the workflow no longer starts with procurement search, so those preferences belong post-BOM, immediately before candidate search.

Updates applied:

- Step 1.2 now documents that `pdf_image_audit.py` produces both the cleaned PDF and `{stem}_clean_page_analysis.json` via `--page-analysis`.
- Step 1.2b now documents candidate selection from combined signals: page size, text density, image coverage, vector drawing/operator metrics, `autocad_like`, `image_heavy`, and anti-scan filters.
- Step 1.2b now documents `replace_page`, `replace_images`, and `leave_for_ocr`, including PNG-based replacement for regions (not PDF text overlays), real rendered image rect resolution, OCR-friendly labels, and compressed PDF saving.
- Gate 0 was reframed as package/document-scope confirmation only.
- Origin/manufacturer and brand preferences moved to a post-BOM `Gate 5 — Preferencias de búsqueda post-BOM`, right before Step 6 candidate search.
- README and `instrucciones/README.md` updated to match.

## 2026-05-19 — Docling extractor alignment and Modal Docling support

Roberto asked to verify the current Docling extractor against the local Docling Serve API guide and add support for the Modal-hosted Docling service.

Findings and changes:

- `scripts/extractors/docling_extract.py` now aligns with the documented Docling Serve API:
  - default base URL `https://docling.infinitek.pe`;
  - sync and async endpoints;
  - `task_status` polling;
  - `document.md_content` extraction;
  - clean Markdown fields `image_export_mode=placeholder`, `include_images=false`;
  - optional `do_ocr`, `force_ocr`, `ocr_lang`, `page_range`, `document_timeout`, `table_mode`.
- Added standard extractor output-dir behavior: `{basename}_docling.md` + `{basename}_docling.json`, while preserving explicit output-file/stdout usage.
- Added health/version checks.
- Added `scripts/extractors/modal_docling_extract.py`, defaulting to `https://rmichelena--docling-converter-fastapi-app.modal.run`, async mode, and `{basename}_modal_docling.*` outputs.
- Added `[docling]` and `[modal_docling]` config stanzas to `extractors.conf.example`.
- Added `docling` and `modal_docling` to `batch_runner.py`.
- Verified actual API behavior: `page_range` is validated as a list of two ints. Scripts accept `--page-range START,END` or `START-END` and emit repeated multipart fields. The API guide notes were updated accordingly.

Verification run:

- Local Docling `/version`: OK, docling-serve 1.18.0 / docling 2.93.0.
- Modal Docling `/version`: OK, same docling-serve/docling versions after cold start.
- Local conversion test: `pliego_absolutorio.pdf` page 1 -> 3,322 chars OK.
- Modal conversion test: same file/page -> 3,322 chars OK via async.
- Batch runner test with `--extractors docling`: full `pliego_absolutorio.pdf` -> 51,292 chars OK.

## 2026-05-19 — Paso 1 defaults: automated docs-to-index pipeline

Roberto clarified the intended default for the early workflow: it should be launchable almost automatically through document conversion and structural indexing.

Decision:

- Paso 1 PDF branch defaults to:
  1. PDF/DOCX triage and DOCX→PDF where needed.
  2. `pdf_image_audit.py --strip --page-analysis` always, to remove repeated headers/footers/decorations and produce per-page content analysis.
  3. `pdf_plan_pages.py` audit/build always, to replace confirmed plans/diagrams/visual regions before Markdown conversion. Empty/OK audit can continue.
  4. `modal_docling_extract.py` as default PDF→Markdown extractor, using `{stem}_preocr.pdf` when present and `{stem}_clean.pdf` otherwise.
  5. Paso 1.5 structural index as default before thematic/BOM extraction.

Fail policy:

- If cleaning, plan/diagram replacement, Modal Docling, or structural indexing fails, stop and ask the human with a short diagnosis and options.
- Do not silently fall back to Docling local, LandingAI, DocAI, MarkItDown, or skipping the failed step.

Additional clarification:

- The old post-Markdown pause is no longer a hard gate. The automated block should continue through structural indexing. Human interruption happens on failure/anomaly, or as an optional post-index spot-check summary.
