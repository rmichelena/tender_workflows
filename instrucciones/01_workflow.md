# 01_workflow.md — Runbook operativo de procurement (v0.2)

> **Regla general**: para cada subpaso, ejecutar exactamente lo indicado: Owner, Tipo, Prompt, Inputs, Modelo/Tools, Tool budget, Outputs, QA/Gate, Criterio Done.
> Selección de modelo: desde `model_routing.yaml` (no de `catalog_modelos.md` directamente — ese describe capacidades; el routing decide quién hace qué basado en evidencia).
> Selección de tools: desde `catalog_tools.md`, respetando primario+fallback.
> Patrones de delegación: ver `agent_patterns.md` — leer antes de delegar.
> **JSON como canónico**: cualquier paso que produce datos estructurados entrega JSON validado contra schema. El orquestador genera los derivados (TSV, MD, XLSX) automáticamente.


---

## Gate 0 — Paquete documental inicial (obligatorio antes de ejecutar)

**Owner**: Orquestador (diálogo con humano)
**Acción**: confirmar el paquete documental y alcance inicial:
- carpeta del proyecto / expediente;
- documentos fuente disponibles;
- si hay documentos externos o anexos pendientes;
- si el run es completo o un experimento acotado a ciertos documentos/ejes.

**Output**: inventario inicial de documentos y alcance del run.
**Done**: paquete documental confirmado por humano.

---

## Paso 1 — Normalización documental (pipeline determinístico)

**Tipo**: workflow no-LLM determinístico + LLM call selectiva como fallback.

**Default operativo Paso 1**:
1. Triage por tipo de archivo.
2. Rama PDF: convertir DOCX→PDF cuando aplique.
3. Ejecutar cleaning **siempre con `--strip` + `--page-analysis`** para remover headers/footers/decoraciones repetitivas y producir reporte por página.
4. Ejecutar detección/análisis/reemplazo de planos, diagramas y regiones visuales antes de convertir a Markdown.
5. Convertir PDFs pre-OCR con **Modal Docling** (`scripts/extractors/modal_docling_extract.py`) como extractor default.
6. Ejecutar **eje 0 libre pre-index** sobre la carpeta de documentos normalizados.
7. Pausar en gate humano para decidir si la licitación interesa y si se continúa.
8. Solo si el humano aprueba continuar: generar índice estructural Paso 1.5 y seguir con el resto del workflow.

**Política de falla**: esta secuencia debe ser casi automática, pero no silenciosa. Si falla cleaning, reemplazo de planos/diagramas, Modal Docling, eje 0 libre o indexación estructural, el orquestador **se detiene y pregunta al humano** con diagnóstico breve, archivos afectados y 2-3 opciones concretas. No degradar automáticamente a otro extractor ni saltar pasos sin aprobación.

### 1.0 Triage de inputs por tipo de archivo

**Owner**: Orquestador (clasificación determinística por extensión).
**Tarea**: para cada archivo en `/proyecto/inputs/`, decidir la rama:
- `.docx` → rama PDF (Paso 1.1).
- `.pdf` → rama PDF (Paso 1.1, pasa sin convertir).
- `.xlsx` / `.xlsm` → **rama XLSX (Paso 1.0.b)** — NO se convierte a PDF.
- Otros (`.txt`, `.csv`, imágenes sueltas): según corresponda; en caso de duda escalar al humano.

**Done**: cada input asignado a una rama.

### 1.0.b Rama XLSX (multi-pestaña → single-tab → markdown/html)

> **Por qué rama separada**: convertir Excel a PDF rompe tablas grandes, merged cells y multi-hoja. Para preservar integridad estructural, los XLSX se procesan nativos con `openpyxl` y se convierten a la representación óptima por hoja (markdown table para tablas planas, HTML table para merged cells jerárquicas, markdown text para hojas narrativas).

**Tipo**: workflow determinístico de 3 sub-pasos.

#### 1.0.b.1 Split multi-pestaña → single-tab

**Owner**: Orquestador (script `scripts/extractors/xlsx_split.py`).
**Tarea**: dividir cada XLSX/XLSM multi-pestaña en N archivos XLSX, uno por hoja, preservando merged cells y formato.

**Outputs**:
- `/proyecto/artifacts/step_1_xlsx_split/{stem}_sheet{NN}_{slug}.xlsx` (uno por hoja)
- `/proyecto/artifacts/step_1_xlsx_split/{stem}_split_manifest.json` (índice de hojas)

**Done**: cada hoja del XLSX original es un archivo independiente.

#### 1.0.b.2 Análisis de hoja → decisión de representación

**Owner**: Orquestador (script `scripts/extractors/xlsx_sheet_analyze.py`).
**Tarea**: por cada XLSX single-tab, analizar contenido y decidir representación óptima.

**Reglas de decisión** (umbrales en `params.yaml → xlsx`):
- Hoja vacía → `empty`.
- Texto largo dominante (long_text_ratio ≥ 0.40 y ≤ 3 columnas) → `markdown_text`.
- Merged cells en filas 1-3 (headers jerárquicos) → `html_table`.
- Merged cells en filas de datos → `html_table`.
- Resto (tabla rectangular sin merged) → `markdown_table`.

**Outputs**: `{xlsx_single_tab_path}_analysis.json` con métricas y decisión.

**Done**: cada hoja tiene su representación decidida.

#### 1.0.b.3 Conversión a markdown/html

**Owner**: Orquestador (script `scripts/extractors/xlsx_convert.py`).
**Tarea**: aplicar el conversor según la representación decidida en 1.0.b.2.

- `markdown_table`: vía MarkItDown (fallback: generación manual si MarkItDown no disponible).
- `html_table`: vía openpyxl + generación HTML nativa preservando colspan/rowspan/scope.
- `markdown_text`: párrafos plain markdown.
- `empty`: archivo placeholder con comentario.

Cada output incluye frontmatter YAML con metadata (sheet_name, representation, max_row/col, merged_count, etc.).

**Outputs**: `/proyecto/artifacts/step_1_normalizados/{stem}_sheet{NN}_{slug}.md` (uno por hoja).

> Las hojas XLSX se mezclan en `step_1_normalizados/` con los markdowns que vienen de la rama PDF (paso 1.3). A partir de ahí, el flujo es uniforme para todos los inputs.

**Done**: todos los XLSX están normalizados a markdown/html en `step_1_normalizados/`.

### 1.1 Conversión a PDF (rama PDF, solo DOCX/PDF)

**Owner**: Orquestador (script determinístico)
**Tarea**: por cada archivo de la rama PDF (DOCX + PDF originales):
- Si es DOCX → convertir a PDF (LibreOffice headless o equivalente).
- Si es ya PDF → pasar sin tocar.

> XLSX/XLSM **NO** entran por acá. Ya están normalizados vía Paso 1.0.b.

**Output**: `/proyecto/artifacts/step_1_pdfs/{nombre_doc}.pdf`
**Done**: todos los inputs de la rama PDF son PDF.

### 1.2 Optimizador + análisis de contenido por página

**Owner**: Orquestador (script `scripts/pdf_image_audit.py` o similar)
**Tarea**: detectar y eliminar zonas repetitivas (headers, footers, watermarks, firmas, sellos, logos decorativos) que aparecen en múltiples páginas. Producir PDF "limpio" y un análisis de contenido por página que alimente Paso 1.2b.

**Default**: ejecutar siempre con `--strip` y `--page-analysis`. No correr en modo audit-only salvo que el humano lo pida explícitamente o se esté diagnosticando un fallo.

**Comando típico**:

```bash
python3 scripts/pdf_image_audit.py input.pdf \
  --strip \
  --output artifacts/step_1_pdfs_clean/{stem}_clean.pdf \
  --report artifacts/step_1_pdfs_clean/{stem}_clean_report.json \
  --page-analysis-output artifacts/step_1_pdfs_clean/{stem}_clean_page_analysis.json
```

**Outputs**:
- `/proyecto/artifacts/step_1_pdfs_clean/{stem}_clean.pdf`
- `/proyecto/artifacts/step_1_pdfs_clean/{stem}_clean_report.json`
- `/proyecto/artifacts/step_1_pdfs_clean/{stem}_clean_page_analysis.json`

**Page analysis** incluye, por página:
- dimensiones/orientación;
- densidad textual;
- conteo/cobertura de imágenes;
- conteo/cobertura de dibujos vectoriales/operadores;
- `content_dominant` (`text`, `vector_drawing`, `image_heavy`, `mixed`, `low_density_large_page`);
- `plan_candidate_signals` (`large_page`, `high_drawing_count`, `high_drawing_ratio`, `autocad_like`, `image_heavy`, `very_low_text_density`, etc.).

**Done**: PDFs optimizados sin elementos repetitivos no útiles + reporte de contenido por página generado.

**Si falla**: detener workflow y preguntar al humano. No continuar hacia OCR/Markdown con PDFs sin cleaning salvo autorización explícita.


### 1.2b Detección, análisis y sustitución de planos/diagramas grandes

**Owner**: Orquestador + subagente visual barato.
**Script**: `scripts/pdf_plan_pages.py`
**Prompt visual**: `prompts/prompt_planos_vision.md`
**Schema**: `schemas/plan_pages_analysis.schema.json`
**Modelo**: `model_routing.yaml → paso_1_2b_planos_vision` (primary: `google/gemini-2.5-flash`).

**Tarea**: después del PDF limpio y antes de la conversión a markdown, detectar páginas/regiones con planos, diagramas, imágenes técnicas o contenido visual que el OCR genérico manejaría mal. La candidatura combina tamaño + análisis de contenido por página; la decisión final la hace un modelo visual.

**Default**: ejecutar este paso para todos los PDFs limpios de la rama PDF, aunque no se esperen planos. Si no hay candidatos, debe producir audit vacío/OK y continuar.

**Método**:
1. Consumir el PDF limpio y, si existe, `{stem}_clean_page_analysis.json` generado por Paso 1.2.
2. Auditar tamaño de páginas, tamaño dominante y área mediana.
3. Marcar candidatos combinando:
   - área/aspect ratio/tamaño absoluto;
   - baja densidad textual;
   - alto conteo o área de dibujos vectoriales/operadores;
   - señales tipo AutoCAD (`autocad_like`);
   - páginas `image_heavy`, con filtros anti-scan.
4. Aplicar filtros para no enviar al visual páginas que probablemente son documento escaneado normal:
   - si demasiadas páginas consecutivas son `image_heavy`;
   - si >70% del documento es `image_heavy`;
   - si la página tiene demasiadas imágenes pequeñas;
   - si las imágenes no ocupan ancho significativo.
5. Rasterizar candidatos a resolución moderada.
6. Pedir al modelo visual clasificar cada candidato como:
   - `replace_page`: página completa es plano/diagrama;
   - `replace_images`: página textual con regiones visuales/diagramas/fotos que conviene sustituir;
   - `leave_for_ocr`: dejar al OCR normal.
7. Si una página confirmada es plano/diagrama completo:
   - extraer `identifier_or_title` visible, ej. `Plano instalaciones eléctricas página 1`, `SPYL-SV-T-0300`, `SPUR-SV-T-0301 — Distribución de datos y voz en el terminal`;
   - describirla brevemente;
   - extraer información explícitamente visible útil para procurement;
   - generar reemplazo textual de página completa.
8. Si una página requiere `replace_images`:
   - identificar regiones con `bbox_pct`;
   - durante el build resolver el bbox aproximado al rect real de la imagen renderizada cuando exista;
   - sustituir la región por una imagen PNG con resumen textual OCR-friendly, no por overlay de texto PDF.
9. Extraer páginas afectadas a PDF separado.
10. Generar un PDF pre-OCR donde las páginas/regiones confirmadas son sustituidas por resúmenes textuales. Páginas candidatas no confirmadas, como tablas grandes o scans normales, quedan intactas.

**Outputs siempre permitidos**:
- `/proyecto/artifacts/step_1_planos/{stem}_page_size_audit.json`
- `/proyecto/artifacts/step_1_planos/{stem}_candidate_pages/page_XXXX.png` (solo si hubo candidatos rasterizados; temporales/auditables)

**Outputs solo si se confirmó al menos un `replace_page` o `replace_images`**:
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.pdf`
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.json`
- `/proyecto/artifacts/step_1_planos/planos_extraidos_{stem}.md`
- `/proyecto/artifacts/step_1_pdfs_preocr/{stem}_preocr.pdf`

Si no se confirmó ninguna extracción/sustitución visual, no generar placeholders tipo “No se confirmaron planos/diagramas…”, ni PDF/MD/JSON `planos_extraidos_*`; Modal Docling debe consumir directamente `{stem}_clean.pdf`.

**Reglas**:
- No borrar nunca páginas del PDF limpio; solo crear derivados.
- No excluir por tamaño, dibujo vectorial o imagen únicamente: requiere confirmación visual.
- No mandar todas las páginas `image_heavy` al visual si el documento parece escaneado completo.
- No inventar cantidades ni códigos no legibles.
- Si una página grande es tabla/anexo textual, dejarla en el flujo OCR normal (`exclude_from_ocr=false`).
- Para `replace_images`, reemplazar con PNG renderizado de fondo gris y etiquetas OCR-friendly (`[imagen reemplazada]`, `[resumen]`, `[texto visible]`, `[notas]`). Evitar overlay de texto PDF.
- Guardar PDFs derivados con compresión (`garbage=4`, `deflate=True`, `deflate_images=True`, `deflate_fonts=True`, `clean=True`) cuando el script lo soporte.
- Modal Docling debe consumir `{stem}_preocr.pdf` si existe; si no existe, consumir `{stem}_clean.pdf`.
- Si el paso falla o el JSON visual no valida, detener workflow y preguntar al humano. No enviar PDFs sin sustitución a Modal Docling cuando había candidatos visuales sin resolver.

### 1.3 OCR + parsing a Markdown con Modal Docling

**Owner**: Orquestador (script `scripts/extractors/modal_docling_extract.py`)
**Tarea**: pasar cada PDF pre-OCR por Modal Docling → Markdown estructurado.

**Default**:
- Usar `scripts/extractors/modal_docling_extract.py`.
- Usar modo async (default del script).
- Input por documento:
  - si existe `/proyecto/artifacts/step_1_pdfs_preocr/{stem}_preocr.pdf`, usar ese;
  - si no existe, usar `/proyecto/artifacts/step_1_pdfs_clean/{stem}_clean.pdf`.
- Output en `/proyecto/artifacts/step_1_normalizados/`.

**Comando típico**:

```bash
python3 scripts/extractors/modal_docling_extract.py \
  /proyecto/artifacts/step_1_pdfs_preocr/{stem}_preocr.pdf \
  --output-dir /proyecto/artifacts/step_1_normalizados
```

**Output**: `/proyecto/artifacts/step_1_normalizados/{sanitize_filename(input)}_modal_docling.md`, donde `sanitize_filename` es la convención de `scripts/extractors/common.py` (NFKD ASCII + caracteres no `[a-zA-Z0-9._-]` reemplazados por `_`). No asumir que espacios o acentos se conservan.
**QA interno**: verificar que el `.md` existe, no está vacío y tiene tamaño razonable frente al PDF fuente; registrar anomalías.
**Done**: todos los inputs tienen su `.md`. Continuar automáticamente a Paso 1.3b salvo falla.

**Si falla**: detener workflow y preguntar al humano. No hacer fallback automático a Docling local, LandingAI, DocAI o MarkItDown sin autorización explícita.

**Fallback autorizado por humano**: según diagnóstico, usar Docling local (`scripts/extractors/docling_extract.py`), LandingAI, DocAI, MarkItDown o LLM vision de página específica (ver `model_routing.yaml → paso_1_vision_fallback`).

### 1.3b Eje 0 libre pre-index — datos generales y gate go/no-go

> Este paso ocurre **antes del índice estructural** porque eje 0 ya se hace como lectura libre/semiestructurada, sin chunks. Su objetivo principal es decidir temprano si vale la pena invertir en indexado, extracción temática, BOM y búsquedas.

**Tipo**: lectura libre híbrida + consolidación/verificación del orquestador.
**Owner**: Orquestador → 1-2 subagentes lectores libres.
**Prompt base**: `prompts/prompt_axis0_free_reader.md` pegado inline en el handoff cuando quepa.
**Inputs**:
- carpeta completa `/proyecto/artifacts/step_1_normalizados/`;
- inventario breve de documentos disponibles;
- opcionalmente PDFs limpios/pre-OCR si el lector necesita confirmar algo visual.

**Tarea**:
1. Leer/buscar libremente en los documentos normalizados del expediente.
2. Extraer en Markdown/semiestructurado los datos generales de la licitación:
   - objeto/alcance con hasta 8 familias principales de bienes/equipos;
   - comprador/cliente y entidades supervisoras relevantes;
   - cronograma e hitos críticos;
   - presupuesto/valor referencial;
   - moneda, garantías, pagos, penalidades;
   - requisitos principales de postor/consorcio/experiencia;
   - clasificación contractual y riesgos/dudas principales;
   - cualquier condición que afecte decisión comercial temprana.
3. El orquestador consolida los outputs, verifica discrepancias críticas contra los documentos fuente y produce un resumen ejecutivo.

**Outputs**:
- `/proyecto/artifacts/step_1_axis0_preindex/axis0_free_reader_{modelo}.md` (si hay múltiples lectores)
- `/proyecto/artifacts/step_1_axis0_preindex/axis0_go_no_go_summary.md`
- opcional: `/proyecto/artifacts/step_1_axis0_preindex/axis0_go_no_go_summary.json` si se requiere normalización canónica posterior.

**Gate 1 — decisión humana de continuidad**:
Presentar `axis0_go_no_go_summary.md` al humano y preguntar explícitamente:

1. **Continuar**: licitación interesa → ejecutar Paso 1.5 indexado y seguir workflow.
2. **Detener**: licitación no interesa → cerrar run conservando artifacts.
3. **Pedir aclaración/revisión puntual**: responder dudas concretas antes de decidir.

**Done**: decisión humana registrada. Solo continuar a Paso 1.5 si la respuesta es **Continuar**.

**Si falla**: detener workflow y preguntar al humano. No saltar directo a indexado/BOM sin decisión explícita.

---

## Paso 1.4 — Incorporar aclaraciones → documentos "aclarados"

**Tipo**: auditoría/revisor de ojos frescos con handoff acotado (ver `agent_patterns.md` §3.5).

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
- Si la indexación falla después del retry permitido, detener workflow y preguntar al humano. No pasar a extracción temática/BOM con documentos sin índice estructural salvo autorización explícita.

**Gate 1 post-index opcional**: al terminar conversión + índices de todos los documentos, el orquestador puede presentar resumen de outputs y anomalías para spot-check humano. Si no hay anomalías y el usuario pidió modo automático, continuar al siguiente bloque del workflow.

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

> Antes de construir BOM consolidado, lanzar lectores especializados por eje y documento. Cada subagente produce **solo JSON canónico**; el orquestador valida y renderiza Markdown determinísticamente.

> Ajuste híbrido: no todos los ejes deben usar el mismo nivel de rigidez en la primera lectura. El eje 0 ya se ejecutó antes del indexado como Paso 1.3b para el gate go/no-go. Paso 2A se concentra en ejes posteriores y/o en canonicalizar eje 0 solo si hace falta.

### 2A.axis0 — Canonicalización opcional del eje 0 ya aprobado

**Uso recomendado**: solo después del Gate 1 si la licitación interesa y se requiere convertir el resumen libre de Paso 1.3b a JSON canónico o enriquecerlo con verificaciones adicionales. No relanzar eje 0 chunked por defecto.

**Patrón**:
1. Usar `/proyecto/artifacts/step_1_axis0_preindex/axis0_go_no_go_summary.md` como fuente primaria.
2. Verificar discrepancias o dudas contra `/proyecto/artifacts/step_1_normalizados/` y PDFs fuente.
3. Producir JSON canónico si un paso posterior lo requiere.
4. No usar chunk plans para eje 0 salvo experimento explícito.

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

> La lectura libre multi-documento propiamente dicha vive en Paso 1.3b; esta sección solo documenta cómo reutilizar/canonicalizar ese resultado después de la decisión de continuidad.

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

## Gate 5 — Preferencias de búsqueda post-BOM

> Este gate ocurre después de tener el BOM preparado para búsqueda. Las preferencias de origen/marca solo son relevantes cuando se van a buscar candidatos.

**Owner**: Orquestador (diálogo con humano)

**Acción**: presentar el BOM de búsqueda y solicitar/confirmar:
- `origen_fabricacion`: países permitidos | vetados | sin preferencia;
- `marcas`: preferidas | vetadas | sin preferencia;
- items `SKIP` o `DIFERIR`;
- cualquier restricción comercial antes de invertir tokens en Paso 6.

**Output**: `/proyecto/overlay_usuario.yaml`
**Done**: overlay guardado/actualizado y confirmado por humano.

Si las preferencias cambian después, actualizar `overlay_usuario.yaml` y relanzar Paso 6 selectivamente para los items afectados.

---

## Paso 6 — Búsqueda de candidatos (ÚNICO paso multi-agent del workflow)

> **Tipo**: búsqueda externa con fan-out paralelo controlado (ver `agent_patterns.md` §3.6).
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
│   ├── step_1_normalizados/               (markdowns post-Modal-Docling/preocr)
│   ├── step_1_axis0_preindex/             (`axis0_go_no_go_summary.md`; gate humano antes de indexar)
│   ├── step_1_aclaradas/                  (docs aclarados + auditoría)
│   ├── step_1_index/                      (`{stem}_index.json/.md`; índice estructural post-gate + correcciones Markdown sugeridas)
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
