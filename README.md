# tender_procurement

Pipeline de extracción y análisis de documentos de licitación pública.

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
│       ├── landingai_extract.py       # LandingAI ADE (recomendado para PDFs mixtos/escaneados)
│       ├── markitdown_extract.py      # MarkItDown (rápido, sin OCR, PDFs vectoriales + DOCX)
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
```

## Quick Start

1. Copia y edita la configuración:
```bash
cp scripts/extractors/extractors.conf.example scripts/extractors/extractors.conf
# Edita extractors.conf con tus valores
```

2. Instala dependencias:
```bash
pip install requests PyMuPDF google-auth-oauthlib markitdown landingai-ade
```

3. Ejecuta un extractor:
```bash
# PDF mixto/escaneado (recomendado):
python3 scripts/extractors/landingai_extract.py documento.pdf

# PDF vectorial puro o DOCX (rápido, sin OCR):
python3 scripts/extractors/markitdown_extract.py documento.pdf output/

# PDF grande escaneado con DocAI (máxima calidad, lento):
python3 scripts/extractors/docai_batch_gcs.py documento.pdf output/
```

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

### 3. Google DocAI — Batch/GCS (máxima calidad, lento)

```bash
python3 scripts/extractors/docai_batch_gcs.py documento.pdf output_dir/
```

- **Velocidad**: ~2 pág/min (43 min para 58 págs con v1.6)
- **OCR**: Sí — OCR + estructura jerárquica + detección de headers/footers
- **Versiones**: v1.0 (rápido, 42s), v1.6 (mejor estructura, 43min)
- **Límites**: Hasta 500 páginas por documento
- **Requisitos**: Bucket GCS + service agent de DocAI
- **Setup**: Ver `docs/docai_setup.md`

### 4. Google DocAI — Online/Chunked (alternativa sin GCS)

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
  --extractors docai_batch,markitdown \
  --recursive
```

## Guía de selección de extractor

| Tipo de documento | Recomendado | Alternativa |
|---|---|---|
| **PDF con páginas/fragmentos escaneados** | LandingAI ADE `dpt-2-latest` | DocAI v1.6 |
| **PDF vectorial puro** (texto embebido) | MarkItDown | LandingAI ADE |
| **DOCX** (sin gráficos críticos) | MarkItDown | — |
| **XLSX / CSV** | MarkItDown | LandingAI ADE |
| **PDF escaneado puro, calidad máxima** | DocAI v1.6 batch | LandingAI ADE |

**Criterio clave**: Si el documento tiene **algo escaneado** (firmas, stamps, páginas escaneadas, diagramas), LandingAI ADE es la mejor relación velocidad/calidad. Si es puramente vectorial/digital, MarkItDown es suficiente.

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
