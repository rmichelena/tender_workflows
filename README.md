# tender_procurement

Pipeline de extracción y análisis de documentos de licitación pública.

## Visión general

El proyecto tiene dos planos:

1. **Workflow conceptual** (`instrucciones/`) — define el pipeline completo de 7 pasos desde EETT/aclaraciones hasta un consolidado de shortlist con matrices de cumplimiento. Es la documentación canónica de qué hace el sistema, cómo se delega a sub-agentes LLM, qué modelo va a qué tarea, qué tools usar.

   Archivos clave a leer en orden:
   - `instrucciones/00_prompt_orquestador.md` — punto de entrada del orquestador.
   - `instrucciones/agent_patterns.md` — patrones de delegación (10 reglas operativas).
   - `instrucciones/01_workflow.md` — runbook de los 7 pasos.
   - `instrucciones/params.yaml`, `model_routing.yaml`, `catalog_tools.md` — parámetros, routing, tools.
   - `instrucciones/prompts/` — plantillas de cada sub-agente.
   - `instrucciones/schemas/` — contratos JSON canónicos.

2. **Implementación parcial** (`scripts/extractors/`) — pipeline real del **Paso 1 (normalización documental)**, ya funcional. El resto del workflow se ejecuta delegando a sub-agentes según `instrucciones/`. Esta sección del README documenta los extractores.

Estado actual: **v0.2**. La v0.1 corrió end-to-end contra ICAO-00068 con resultado desigual (10.7% hit rate). v0.2 incorpora aprendizajes documentados en `REVIEW_FRESH_EYES.md`, `MEJORAS_PROPUESTAS.md` y los autoevaluaciones en `proyecto/logs/`.

## Estructura

```
tender_procurement/
├── README.md                          # Este archivo
├── .gitignore                         # Ignora output/, ejemplos, config local
├── scripts/
│   └── extractors/
│       ├── common.py                  # Módulo compartido (config, creds, post-procesadores)
│       ├── extractors.conf.example    # Template de configuración
│       ├── extractors.conf            # Config local (gitignored)
│       ├── docling_extract.py         # Docling Serve local/bare-metal
│       ├── modal_docling_extract.py   # Modal Docling (default workflow PDF→Markdown)
│       ├── landingai_extract.py       # LandingAI ADE (fallback autorizado)
│       ├── markitdown_extract.py      # MarkItDown (rápido, sin OCR, pruebas/fallback)
│       ├── docai_online.py            # Google DocAI modo online/chunked
│       ├── docai_batch_gcs.py         # Google DocAI modo batch con GCS
│       ├── batch_runner.py            # Runner: ejecuta N extractores sobre N PDFs
│       └── .env_landingai             # API key LandingAI (gitignored)
├── docs/
│   ├── extractor_benchmark.md         # Benchmark comparativo completo
│   └── docai_setup.md                 # Setup de Google DocAI + GCS
├── output/                            # GITIGNORED — resultados de extracción
├── ejemplos/                          # GITIGNORED — datos de muestra
│   ├── documentos_muestra/
│   └── extractor_benchmark/
└── instrucciones/                     # Workflow y prompts del pipeline
    ├── prompts/prompt_planos_vision.md
    ├── prompts/prompt_document_indexer.md
    ├── schemas/plan_pages_analysis.schema.json
    └── schemas/document_index.schema.json
```

Convención de nombres: los PDFs optimizados del Paso 1 se guardan en `artifacts/step_1_pdfs_clean/` con sufijo explícito `_clean.pdf` (`documento_clean.pdf`). La carpeta ya indica que son limpios, pero el sufijo evita ambigüedad cuando se copian, adjuntan o procesan fuera de esa carpeta.

## Quick Start

1. Copia y edita la configuración:
```bash
cp scripts/extractors/extractors.conf.example scripts/extractors/extractors.conf
# Edita extractors.conf con tus valores
```

2. Desde la raíz de este repo (`tender_procurement/`), instala dependencias en un entorno virtual local del tooling:
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install requests PyMuPDF google-auth-oauthlib markitdown landingai-ade
```

> Nota para agentes futuros: en hosts Debian/Ubuntu modernos, Python puede estar en modo PEP 668 (`externally-managed-environment`), por lo que `pip install` contra el Python global puede fallar o ser mala idea. Usar `.venv/` en la raíz de este repo evita tocar paquetes administrados por `apt` y evita crear miles de archivos por cada carpeta de licitación/Dropbox. `.venv/` está gitignored.
>
> Versiones instaladas y verificadas el 2026-05-15 en la corrida AdP Cableado: `PyMuPDF==1.27.2.3`, `landingai-ade==1.12.0`, `markitdown==0.1.5`. Eran las versiones actuales instaladas por `pip` ese día; si un extractor o script deja de funcionar por cambios upstream, intentar primero pinear estas versiones o revisar compatibilidad de PyMuPDF/MuPDF.

3. Ejecuta un extractor:
```bash
# PDF mixto/escaneado (recomendado):
.venv/bin/python scripts/extractors/landingai_extract.py documento.pdf

# PDF vectorial puro o DOCX (rápido, sin OCR):
.venv/bin/python scripts/extractors/markitdown_extract.py documento.pdf output/

# Docling local/bare-metal (default parser OCR/Markdown):
.venv/bin/python scripts/extractors/docling_extract.py documento.pdf output/ --async

# Docling en Modal (misma API, async por defecto):
.venv/bin/python scripts/extractors/modal_docling_extract.py documento.pdf output/

# PDF grande escaneado con DocAI (máxima calidad, lento):
.venv/bin/python scripts/extractors/docai_batch_gcs.py documento.pdf output/
```

## Paso 1.2 / 1.2b default: contenido por página + planos/diagramas antes de OCR

Después de limpiar PDFs y antes de Modal Docling, el workflow detecta páginas o regiones visuales —planos, diagramas, fotografías técnicas o páginas CAD/vectoriales— para evitar costo/ruido en conversores Markdown.

Default del workflow:

1. Ejecutar cleaning con `--strip --page-analysis`.
2. Ejecutar `pdf_plan_pages.py audit` y `build` para sustituir planos/diagramas confirmados.
3. Convertir a Markdown con `modal_docling_extract.py`.
4. Indexar estructura del Markdown.

Si falla cleaning, sustitución de planos/diagramas, Modal Docling o indexación, el workflow debe detenerse y preguntar al usuario. No hay fallback automático silencioso.

El optimizador `pdf_image_audit.py` también puede producir un análisis de contenido por página. Ese reporte alimenta la detección de candidatos visuales: no se decide solo por tamaño.

Ejemplo de limpieza + análisis:

```bash
.venv/bin/python scripts/pdf_image_audit.py input.pdf \
  --strip \
  --output artifacts/step_1_pdfs_clean/input_clean.pdf \
  --report artifacts/step_1_pdfs_clean/input_clean_report.json \
  --page-analysis
```

Esto genera, además del PDF limpio:

- `artifacts/step_1_pdfs_clean/{stem}_clean_report.json`
- `artifacts/step_1_pdfs_clean/{stem}_clean_page_analysis.json`

El `page_analysis` incluye tamaño, densidad textual, conteo/cobertura de imágenes, conteo/cobertura de dibujos vectoriales/operadores, `content_dominant` y `plan_candidate_signals`.

Herramienta:

```bash
.venv/bin/python scripts/pdf_plan_pages.py audit input_clean.pdf --output-dir artifacts/step_1_planos
# analizar imágenes candidatas con prompts/prompt_planos_vision.md
.venv/bin/python scripts/pdf_plan_pages.py build input_clean.pdf \
  --output-dir artifacts/step_1_planos \
  --preocr-dir artifacts/step_1_pdfs_preocr \
  --analysis-json artifacts/step_1_planos/planos_extraidos_DOC.json
```

Outputs principales:

- `artifacts/step_1_planos/{stem}_page_size_audit.json`
- `artifacts/step_1_planos/planos_extraidos_{stem}.pdf/.json/.md`
- `artifacts/step_1_pdfs_preocr/{stem}_preocr.pdf`

El análisis visual debe extraer `identifier_or_title` cuando exista: número de plano, título del cajetín, o ambos.

La candidatura visual combina señales geométricas y de contenido:

- tamaño/área/aspect ratio;
- baja densidad textual;
- alto conteo/área de dibujos vectoriales;
- señales tipo AutoCAD;
- páginas `image_heavy`, con filtros anti-scan.

El análisis visual decide entre:

- `replace_page`: reemplazar página completa por resumen textual;
- `replace_images`: reemplazar solo regiones/imágenes dentro de una página textual;
- `leave_for_ocr`: dejar al OCR normal.

Para `replace_images`, el build debe insertar una imagen PNG de reemplazo con texto OCR-friendly, no texto overlay PDF. El script resuelve el bbox aproximado al rect real de imagen renderizada cuando puede.

## Paso 1.5 default: índice estructural de Markdown

Tras convertir documentos a Markdown, el workflow ejecuta una pasada de indexación estructural antes de extraer BOM:

- lee TODO el Markdown por ventanas de 200 líneas con overlap de 50;
- reconstruye secciones reales sin confiar ciegamente en headings Markdown;
- genera `artifacts/step_1_index/{stem_original}_index.json` y `.md`;
- usa `instrucciones/schemas/document_index.schema.json`;
- usa `instrucciones/prompts/prompt_document_indexer.md`;
- registra `markdown_corrections_suggested` para reparaciones estructurales de bajo riesgo, pero no modifica el Markdown fuente.

La reparación Markdown, si se aplica, debe ser opcional, reversible y sobre una copia (`step_1_repaired/`), no sobre el archivo normalizado original.

## Extractores

### 1. LandingAI ADE ⭐ Recomendado para PDFs con escaneados

```bash
python3 scripts/extractors/landingai_extract.py documento.pdf
python3 scripts/extractors/landingai_extract.py documento.pdf --model dpt-2-latest
python3 scripts/extractors/landingai_extract.py documento.pdf --no-clean
```

- **Velocidad**: ~100 pág/min (32s para 58 págs)
- **OCR**: Sí — OCR completo con estructura preservada
- **Modelos**: `dpt-2-mini` (rápido, barato), `dpt-2-latest` (mejor calidad)
- **Formatos**: PDF, imágenes (PNG, JPG, etc.), XLSX, CSV. **No DOCX**.
- **Post-procesamiento**: `clean_ade_output()` elimina firmas/logos/attestations, fix NUL→°
- **Costo**: ~87 créditos (mini) / ~174 créditos (v2-latest) por 58 págs
- **Instalación**: `pip install landingai-ade` + API key en `.env_landingai`

### 2. MarkItDown — Para PDFs vectoriales y DOCX

```bash
python3 scripts/extractors/markitdown_extract.py documento.pdf output_dir/
```

- **Velocidad**: ~150 pág/min
- **OCR**: No — solo texto embebido
- **Formatos**: PDF vectorial, DOCX, PPTX, XLSX, imágenes
- **Límites**: Sin límite de páginas
- **Instalación**: `pip install markitdown`

### 3. Docling Serve — Parser/OCR self-hosted

```bash
python3 scripts/extractors/docling_extract.py documento.pdf output/ --async
python3 scripts/extractors/docling_extract.py documento.pdf output/ --page-range 1,50 --async
python3 scripts/extractors/docling_extract.py --version
```

- **Endpoint default**: `https://docling.infinitek.pe`
- **API guide**: `scripts/extractors/api guide references/docling-api-guide.md`
- **OCR**: Sí, vía Docling/RapidOCR según configuración del servicio.
- **Formatos**: PDF, DOCX, PPTX, XLSX y otros soportados por Docling Serve.
- **Salida estándar**: `{nombre}_docling.md` + `{nombre}_docling.json`.
- **Parámetros default**: `image_export_mode=placeholder`, `include_images=false`.
- **Chunks**: `--page-range START,END` envía el rango como lista multipart (`page_range=START`, `page_range=END`), que es lo que valida la API actual.
- **Uso recomendado**: async para PDFs grandes o escaneados; sync solo para documentos pequeños.

### 4. Modal Docling — Parser/OCR default serverless

```bash
python3 scripts/extractors/modal_docling_extract.py documento.pdf output/
python3 scripts/extractors/modal_docling_extract.py documento.pdf output/ --page-range 1,50
python3 scripts/extractors/modal_docling_extract.py --version --timeout 180
```

- **Endpoint default**: `https://rmichelena--docling-converter-fastapi-app.modal.run`
- **API guide**: `scripts/extractors/api guide references/docling-modal-api-guide.md`
- **API**: idéntica al Docling local/bare-metal.
- **Modo default**: async, porque Modal tiene timeout corto en web endpoints y puede tener cold start.
- **Salida estándar**: `{nombre}_modal_docling.md` + `{nombre}_modal_docling.json`.
- **Uso recomendado**: extractor default del workflow para convertir PDFs pre-OCR a Markdown. Si falla, detener y pedir decisión antes de probar otro extractor.

### 5. Google DocAI — Batch/GCS (máxima calidad, lento)

```bash
python3 scripts/extractors/docai_batch_gcs.py documento.pdf output_dir/
```

- **Velocidad**: ~2 pág/min (43 min para 58 págs con v1.6)
- **OCR**: Sí — OCR + estructura jerárquica + detección de headers/footers
- **Versiones**: v1.0 (rápido, 42s), v1.6 (mejor estructura, 43min)
- **Límites**: Hasta 500 páginas por documento
- **Requisitos**: Bucket GCS + service agent de DocAI
- **Setup**: Ver `docs/docai_setup.md`

### 6. Google DocAI — Online/Chunked (alternativa sin GCS)

```bash
python3 scripts/extractors/docai_online.py documento.pdf output_dir/
```

- **Velocidad**: ~16 pág/min
- **Límites**: Procesa en chunks de 15 páginas
- **⚠️**: Prefiera `docai_batch_gcs.py` para docs >15 páginas

### Batch Runner (ejecutar múltiples extractores)

```bash
python3 scripts/extractors/batch_runner.py \
  --input-dir ./pdfs \
  --output-dir ./results \
  --extractors modal_docling,markitdown \
  --recursive
```

## Guía de selección de extractor

| Tipo de documento | Recomendado | Alternativa |
|---|---|---|
| **Flujo default Paso 1** | strip-cleaning + reemplazo planos/diagramas + Modal Docling | Docling local autorizado por humano |
| **PDF con páginas/fragmentos escaneados** | Modal Docling sobre PDF pre-OCR | Docling local / LandingAI / DocAI con aprobación |
| **PDF vectorial puro** (texto embebido) | Modal Docling en workflow completo | MarkItDown para pruebas rápidas |
| **DOCX** (sin gráficos críticos) | MarkItDown | — |
| **XLSX / CSV** | Rama XLSX nativa (`xlsx_split/analyze/convert`) | MarkItDown para pruebas rápidas |
| **PDF escaneado puro, calidad máxima** | Modal Docling por defecto | DocAI v1.6 batch con aprobación |

**Criterio clave del workflow**: Paso 1 ya no elige extractor caso por caso al inicio. Primero limpia (`--strip`), detecta/sustituye planos y diagramas, y luego convierte con Modal Docling. Si algo falla, se detiene y pide decisión humana antes de degradar a otro extractor.

## Post-procesadores (common.py)

Los extractores aplican automáticamente estos post-procesadores:

- **`clean_ade_output()`** — Elimina bloques `<::...::>` de LandingAI (attestations/firmas, logos, decorations), convierte NUL → °
- **`fix_ligatures()`** — Corrige ligatures Unicode (fi→fi) y artefactos LaTeX (`\times`→×, `^{circ}`→°)
- **`fix_chunk_spacing()`** — Reinserta saltos de línea donde DocAI concatena headings con body text

## Comparación de extractores

Ver `docs/extractor_benchmark.md` para el benchmark completo con datos.

**Resumen (58 págs, documento mixto digital+escaneado)**:

| Métrica | LandingAI dpt-2-latest | LandingAI dpt-2-mini | DocAI v1.6 | DocAI v1.0 |
|---|---|---|---|---|
| **Tiempo** | 32s | 33s | 43 min | 42s |
| **Chars (clean)** | 166K | 180K | 122K | 125K |
| **OCR escaneado** | ✅ | ✅ | ✅ | ✅ |
| **Créditos** | 174 | 87 | ~$1.5 | ~$0.5 |

## Configuración

Ver `scripts/extractors/extractors.conf.example`. Copiar a `extractors.conf` y editar.

Variables configurables:
- `project_id`, `location`, `processor_id` — Google Cloud / DocAI
- `gcs_bucket` — bucket para batch mode
- `token_path` — path al archivo OAuth token (relativo o absoluto)
- `online_page_limit` — máx páginas por request online (default: 15)
- `chunk_size` — DocAI chunking config (default: 500)
- `poll_interval`, `max_wait` — polling batch operation
- `[docling]` / `[modal_docling]` — endpoints, timeouts, OCR/table params de Docling Serve
- `step_1_defaults` en `instrucciones/params.yaml` — defaults operativos del pipeline documental

**LandingAI**: API key en `scripts/extractors/.env_landingai` como `VISION_AGENT_API_KEY=...`

## Formato de salida

Todos los extractores producen:

- **`{nombre}_{extractor}.md`** — Markdown con texto extraído
- **`{nombre}_{extractor}.json`** — JSON estructurado con metadata + chunks

### Estructura de chunks (DocAI)

El texto extraído por DocAI viene en `chunkedDocument.chunks`:
- Cada chunk tiene `content` (texto en markdown), `pageSpan` (rango de páginas)
- Los chunks se solapan intencionalmente (`includeAncestorHeadings: true`)
- Deduplicación usa `(chunk_id, content)` para preservar texto legítimamente repetido

### Estructura de chunks (LandingAI ADE)

El texto viene como lista de chunks con tipo:
- `text` — texto normal
- `table` — tablas (HTML)
- `attestation` — firmas (eliminadas por `clean_ade_output`)
- `logo` — logos (eliminados por `clean_ade_output`)
- `figure` — figuras/diagramas
- `marginalia` — notas marginales

## Paso 2A — Lectores temáticos

El flujo experimental actual para extracción temprana usa lectores temáticos por documento/eje:

1. `scripts/build_section_chunks.py` genera `artifacts/step_2_chunks/{stem}_chunks.json` desde el índice Paso 1.5.
2. Cada subagente lee un documento + índice + chunk plan y escribe solo JSON canónico (`thematic_extraction.schema.json`).
3. El orquestador valida con `scripts/validate_thematic_extraction.py`.
4. El orquestador renderiza Markdown humano con `scripts/render_thematic_md.py`.

No pedir Markdown a los subagentes; es derivado determinístico.

### Schemas por eje

Paso 2A usa un shape común (`thematic_extraction.schema.json`), pero los subagentes deben recibir el schema específico del eje para restringir enums y evitar sobre-inclusión:

- `instrucciones/schemas/axis_0_main_tender_data.schema.json`
- `instrucciones/schemas/axis_1_proposal_signature_documents.schema.json`
- `instrucciones/schemas/axis_2_execution_documentary_deliverables.schema.json`
- `instrucciones/schemas/axis_3_execution_services_obligations.schema.json`
- `instrucciones/schemas/axis_4_goods_licenses_equipment.schema.json`

Ejemplo: en eje 4 no existe `phase=proposal`; una mención en formato/presupuesto de propuesta sigue siendo un bien de ejecución, con `source_context_type` adecuado.

#### Eje 4: COTS vs custom-made

El schema de bienes no enumera familias específicas de equipamiento. En su lugar:

- `entry_type` = categoría genérica de procurement.
- `item_name` / `item_family` = texto libre para dedupe/búsqueda.
- `supply_model` distingue COTS, custom-made, configured COTS, mixed o unclear.
- `custom_spec_relevance` indica si la validación debe esperar match de ficha técnica o requisitos de diseño del proyecto.

### Prompt template + payload por eje

No usar un único prompt genérico para todos los ejes. El contrato común vive en:

- `instrucciones/prompts/prompt_thematic_reader.template.md`

La misión/adherencia de cada eje vive en payloads JSON:

- `instrucciones/payloads/thematic_axes/axis_0_main_tender_data.json`
- `instrucciones/payloads/thematic_axes/axis_1_proposal_signature_documents.json`
- `instrucciones/payloads/thematic_axes/axis_2_execution_documentary_deliverables.json`
- `instrucciones/payloads/thematic_axes/axis_3_execution_services_obligations.json`
- `instrucciones/payloads/thematic_axes/axis_4_goods_licenses_equipment.json`

Renderizar prompts con:

```bash
python3 scripts/render_thematic_prompt.py \
  --payload instrucciones/payloads/thematic_axes/axis_4_goods_licenses_equipment.json \
  --output instrucciones/prompts/rendered_thematic/axis_4_goods_licenses_equipment.md
```

Para llamadas JSON-only estrictas, los subagentes deben recibir el prompt renderizado y el schema específico del eje.

Para prompts cortos o de lectura libre (por ejemplo `prompt_axis0_free_reader.md`), el orquestador debe pegar el prompt **inline** en el mensaje del subagente en vez de pasar solo la ruta. La ruta puede quedar como referencia/versionado, pero no debe ser una indirección obligatoria para el modelo.

En lectura libre, especialmente eje 0, el input normal debe ser la **carpeta del expediente/documentos fuente**, no un único documento de bases. El subagente debe buscar en bases, documentos técnicos, anexos, formularios y aclaraciones según existan. Solo limitar a un archivo cuando el orquestador lo indique explícitamente para un experimento o comparación.
