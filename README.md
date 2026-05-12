# tender_procurement

Pipeline de extracción y análisis de documentos de licitación pública.

## Estructura

```
tender_procurement/
├── README.md                          # Este archivo
├── .gitignore                         # Ignora ejemplos y config local
├── scripts/
│   └── extractors/
│       ├── common.py                  # Módulo compartido (config, creds, utils)
│       ├── extractors.conf.example    # Template de configuración
│       ├── extractors.conf            # Config local (gitignored)
│       ├── batch_runner.py            # Runner: ejecuta N extractores sobre N PDFs
│       ├── markitdown_extract.py      # Extractor rápido (sin OCR)
│       ├── docai_online.py            # Google DocAI modo online/chunked
│       └── docai_batch_gcs.py         # Google DocAI modo batch con GCS (recomendado)
├── docs/
│   ├── extractor_benchmark.md         # Benchmark comparativo de extractores
│   └── docai_setup.md                 # Setup de Google DocAI + GCS
└── ejemplos/                          # GITIGNORED — datos de muestra
    ├── documentos_muestra/            # PDFs originales
    └── extractor_benchmark/
        └── outputs/                   # Resultados de extracción
```

## Quick Start

1. Copia y edita la configuración:
```bash
cp scripts/extractors/extractors.conf.example scripts/extractors/extractors.conf
# Edita extractors.conf con tus valores
```

2. Instala dependencias:
```bash
pip install requests PyMuPDF google-auth-oauthlib markitdown
```

3. Ejecuta un extractor:
```bash
python3 scripts/extractors/markitdown_extract.py documento.pdf output/
python3 scripts/extractors/docai_batch_gcs.py documento_grande.pdf output/
```

## Extractores

### 1. MarkItDown (recomendado para docs con texto embebido)

```bash
python3 scripts/extractors/markitdown_extract.py documento.pdf output_dir/
```

- **Velocidad**: ~150 pág/min
- **OCR**: No — solo texto embebido
- **Límites**: Sin límite de páginas
- **Instalación**: `pip install markitdown`

### 2. Google DocAI — Batch/GCS (recomendado para docs grandes o escaneados)

```bash
python3 scripts/extractors/docai_batch_gcs.py documento.pdf output_dir/
```

- **Velocidad**: ~17 pág/min (batch processing)
- **OCR**: Sí — OCR completo + estructura + anotaciones de imagen
- **Límites**: Hasta 500 páginas por documento
- **Requisitos**: Bucket GCS + service agent de DocAI configurado
- **Setup**: Ver `docs/docai_setup.md`

### 3. Google DocAI — Online/Chunked (alternativa sin GCS)

```bash
python3 scripts/extractors/docai_online.py documento.pdf output_dir/
```

- **Velocidad**: ~16 pág/min (procesamiento secuencial por chunks)
- **OCR**: Sí
- **Límites**: Procesa en chunks de 15 páginas
- **Requisitos**: Solo OAuth token
- **⚠️**: Prefiera `docai_batch_gcs.py` para docs >15 páginas

### Batch Runner (ejecutar múltiples extractores)

```bash
python3 scripts/extractors/batch_runner.py \
  --input-dir ./pdfs \
  --output-dir ./results \
  --extractors docai_batch,markitdown \
  --recursive
```

Escribe resultados incrementalmente — no pierde trabajo si se interrumpe.

## Comparación de extractores

- **MarkItDown**: ~150 pág/min, sin OCR, texto embebido, sin límite
- **DocAI Batch**: ~17 pág/min, OCR completo, estructura, 500 págs máx
- **DocAI Online**: ~16 pág/min, OCR completo, 15 págs/chunk

**Resultado benchmark (329 páginas, 6 documentos)**:
- MarkItDown: 896K chars total
- DocAI: 1,368K chars total (**53% más contenido**)
- En docs escaneados: DocAI extrae hasta **2.3x más**

## Configuración

Ver `scripts/extractors/extractors.conf.example`. Copiar a `extractors.conf` y editar.

Variables configurables:
- `project_id`, `location`, `processor_id` — Google Cloud / DocAI
- `gcs_bucket` — bucket para batch mode
- `token_path` — path al archivo OAuth token (relativo o absoluto)
- `online_page_limit` — máx páginas por request online (default: 15)
- `chunk_size` — DocAI chunking config (default: 500)
- `poll_interval`, `max_wait` — polling batch operation

## Formato de salida

Todos los extractores producen:

- **`{nombre}_{extractor}.md`** — Markdown con texto extraído
- **`{nombre}_{extractor}.json`** — JSON estructurado con metadata + chunks + layout blocks

### Estructura de chunks (DocAI)

El texto extraído por DocAI viene en `chunkedDocument.chunks`:
- Cada chunk tiene `content` (texto en markdown), `pageSpan` (rango de páginas)
- Los chunks se solapan intencionalmente (`includeAncestorHeadings: true`)
- Deduplicación usa `(chunk_id, content)` para preservar texto legítimamente repetido
