# Kreuzberg API Guide

Servicio Docker oficial de Kreuzberg para extracción de texto/Markdown desde PDFs, Office, imágenes, email, HTML, archivos comprimidos y otros formatos.

## Servicio

- Imagen: `ghcr.io/kreuzberg-dev/kreuzberg:core`
- Versión probada: `4.9.7`
- Compose: `/home/sysop/compose/kreuzberg/docker-compose.yml`
- Contenedor: `kreuzberg-api`
- Interno Docker: `http://kreuzberg-api:8000`
- Traefik preparado: `https://kreuzberg.infinitek.pe` *(pendiente DNS; actualmente no resuelve desde el servidor)*

## Health

```bash
curl http://kreuzberg-api:8000/health
```

Respuesta típica:

```json
{
  "status": "healthy",
  "version": "4.9.7",
  "plugins": {
    "ocr_backends_count": 3,
    "ocr_backends": ["tesseract", "paddle-ocr", "vlm"],
    "extractors_count": 41,
    "post_processors_count": 4
  }
}
```

## Extraer Markdown

Endpoint:

```http
POST /extract
Content-Type: multipart/form-data
```

Campos:

- `files`: archivo a procesar. Repetible para varios archivos.
- `output_format`: `plain`, `markdown`, `djot`, `html`. Para nuestro caso usar `markdown`.
- `config`: JSON opcional para OCR/config overrides.

Ejemplo:

```bash
curl -sS -X POST \
  http://kreuzberg-api:8000/extract \
  -F 'files=@documento.pdf' \
  -F 'output_format=markdown' \
  -o result.json
```

La respuesta es JSON array. El Markdown está en `.[0].content`.

Ejemplo con jq:

```bash
curl -sS -X POST \
  http://kreuzberg-api:8000/extract \
  -F 'files=@documento.pdf' \
  -F 'output_format=markdown' \
| jq -r '.[0].content' > documento.md
```

## OCR

Para forzar OCR:

```bash
curl -sS -X POST \
  http://kreuzberg-api:8000/extract \
  -F 'files=@scanned.pdf' \
  -F 'output_format=markdown' \
  -F 'config={"ocr":{"language":"spa"},"force_ocr":true}' \
| jq -r '.[0].content' > scanned.md
```

Idiomas OCR incluidos en la imagen core según docs: `eng`, `spa`, `fra`, `deu`, `ita`, `por`, `chi-sim`, `chi-tra`, `jpn`, `ara`, `rus`, `hin`.

## Benchmarks rápidos

### imapsync_2020_gulliver_fdln.pdf

- Tamaño: ~215 KB
- Tiempo: ~108 ms
- RAM idle/después: ~9 MB
- Markdown OK

### 14. Bases_Estandar_LP_Bienes_20230330_152431_281.pdf

- Tamaño: ~2 MB
- Páginas detectadas: 58
- Tiempo: ~700 ms
- Contenido Markdown: ~84 KB
- RAM después: ~37 MB
- Markdown OK

## Comparación inicial

Kreuzberg es muchísimo más liviano y rápido en PDFs con texto embebido que Docling/OpenDataLoader hybrid. Para OCR real hay que probar con PDFs escaneados; allí probablemente subirá CPU/RAM, pero el baseline es excelente.

## Nota CORS

No fijar `KREUZBERG_CORS_ORIGINS="*"`: en v4.9.7 provoca panic por bug/config edge case. Si se omite, Kreuzberg permite all origins por default y arranca correctamente, aunque loguea warning CSRF. Para producción pública conviene fijar origen explícito real cuando sepamos el frontend.
