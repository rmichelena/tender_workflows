# Docling Serve API — Guía de uso para agentes

**Endpoint base:** `https://docling.infinitek.pe`

## Conversión sincrónica (bloquea hasta terminar)

### POST `/v1/convert/file`

Convierte un archivo (PDF, DOCX, PPTX, XLSX, HTML, imágenes, etc.) a Markdown.

**Request:** `multipart/form-data`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `files` | file | **Requerido.** Archivo a convertir. |
| `image_export_mode` | string | `embedded` (default), `placeholder`, o `referenced`. Usar `placeholder` para markdown sin imágenes. |
| `include_images` | string/bool | `false` para excluir imágenes del output. |
| `from_formats` | string[] | Formatos de entrada permitidos. Default: todos. |
| `to_formats` | string[] | Formatos de salida. Default: `["md"]`. Opciones: `md`, `json`, `yaml`, `html`, `text`, `doctags`. |

**Ejemplo (curl):**
```bash
curl -X POST https://docling.infinitek.pe/v1/convert/file \
  -F "files=@/ruta/al/archivo.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false" \
  -o resultado.json
```

**Respuesta:** JSON array. Cada elemento tiene:
- `document.md_content` — Markdown extraído
- `document.title` — Título detectado
- `status` — `success` o `failure`

## Conversión asíncrona (recomendado para archivos grandes)

### 1. Enviar job: `POST /v1/convert/file/async`

Mismos parámetros que el sync. Devuelve inmediatamente:
```json
{"task_id": "abc123"}
```

### 2. Consultar estado: `GET /v1/status/poll/{task_id}`

```json
{"status": "processing"}  // o "success", "failure"
```

### 3. Obtener resultado: `GET /v1/result/{task_id}`

Mismo formato que el sync.

## Parámetros recomendados para uso general

```
image_export_mode=placeholder
include_images=false
```

Esto produce markdown limpio sin imágenes base64 embebidas. Las imágenes aparecen como `<!-- image -->` en el texto.

## Monitoreo y limpieza

- `GET /health` — Health check
- `GET /version` — Versión de docling
- `GET /v1/memory/stats` — Uso de memoria
- `GET /v1/clear/converters` — Liberar memoria de convertidores cacheados
- `GET /v1/clear/results` — Limpiar resultados cacheados

## Notas

- El contenedor consume ~1.3 GB RAM idle, ~2.5 GB bajo carga
- PDFs con OCR: 5-15 segundos por página dependiendo de complejidad
- DOCX/PPTX: más rápido (no necesita OCR), típicamente 1-5 segundos
- No hay autenticación — el servicio está detrás de Traefik pero sin auth adicional
- Soporta hasta ~100 MB por archivo (limitado por memoria disponible)
