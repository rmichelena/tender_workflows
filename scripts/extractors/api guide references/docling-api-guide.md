# Docling Serve API — Guía de uso para agentes

**Endpoint base externo:** `https://docling.infinitek.pe`

**Endpoint base interno recomendado para Hermes:** `http://docling:5001`

Usar el endpoint interno **solo si el contenedor que llama (por ejemplo `hermes`) está en la misma Docker network que `docling`**. Actualmente ambos están conectados a `traefik_net`, por lo que Hermes puede llamar directo a `http://docling:5001`.

El endpoint externo va detrás de Traefik con TLS. Todos los endpoints están en el mismo host/puerto HTTPS. Para agentes/servicios dentro del mismo VPS, preferir el endpoint interno: evita DNS, TLS, Traefik y timeouts de proxy.

## Recomendación operativa

- Para documentos pequeños/medianos: se puede usar sync.
- Para PDFs grandes o escaneados: usar async.
- Para PDFs muy grandes o con muchas páginas escaneadas: procesar por rangos de páginas (`page_range`) para evitar OOM/restarts.

El contenedor corre en CPU. Un PDF escaneado grande puede consumir mucha RAM durante OCR/layout.

## Parámetros recomendados para Markdown limpio

Enviar siempre estos form fields en `multipart/form-data`:

```text
image_export_mode=placeholder
include_images=false
```

Esto evita imágenes base64 inline. Las imágenes quedan como `<!-- image -->`. El texto de imágenes escaneadas se extrae por OCR si `do_ocr=true` (default).

Opcionales útiles:

```text
do_ocr=true              # default; mantener para PDFs escaneados
force_ocr=false          # default; no re-OCR si ya hay texto embebido
ocr_lang=es,en           # opcional; idiomas esperados
page_range=1,50          # opcional; procesar solo páginas 1-50
document_timeout=1800    # opcional; timeout interno por documento, segundos
table_mode=accurate      # default; usar fast si necesitas menor RAM/tiempo
```

## Conversión sincrónica

### `POST /v1/convert/file`

Bloquea hasta terminar. Ojo: vía Traefik puede terminar en `504 Gateway Timeout` si el documento demora demasiado. Para documentos grandes, usar async.

Ejemplo:

```bash
curl -s -X POST http://docling:5001/v1/convert/file \
  -F "files=@/ruta/al/documento.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false" \
  -o resultado.json
```

Respuesta: JSON con `document.md_content`.

## Conversión asíncrona

### 1) Crear task

`POST /v1/convert/file/async`

```bash
curl -s -X POST http://docling:5001/v1/convert/file/async \
  -F "files=@/ruta/al/documento.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false"
```

Respuesta real:

```json
{
  "task_id": "7415118f-70c6-421c-9f64-2639f6de2097",
  "task_type": "convert",
  "task_status": "pending",
  "task_position": 1,
  "task_meta": null,
  "error_message": null
}
```

Importante: el campo es **`task_status`**, no `status`.

### 2) Poll status

`GET /v1/status/poll/{task_id}`

Estados observados:

- `pending`
- `started`
- `success`
- `failure`

Ejemplo:

```bash
curl -s http://docling:5001/v1/status/poll/$TASK_ID
```

### 3) Obtener resultado

Solo cuando `task_status == "success"`:

```bash
curl -s http://docling:5001/v1/result/$TASK_ID -o resultado.json
```

Luego extraer markdown desde `document.md_content`.

## Importante: tareas async y restarts

Los resultados async están en memoria del proceso. Si el contenedor se reinicia (por OOM, despliegue, etc.), el task desaparece y el status/result devuelve:

```json
{"detail":"Task not found."}
```

Si eso pasa, reenviar el documento. Para PDFs muy grandes, reenviar por rangos de páginas.

## Procesar PDFs grandes por chunks

Para un PDF grande escaneado, usar `page_range` y concatenar los markdowns:

```bash
# páginas 1-50
# Nota: la API actual valida page_range como lista de dos enteros. En curl usar
# dos campos repetidos; en los scripts usar --page-range 1,50.
-F "page_range=1" \
-F "page_range=50"

# páginas 51-100
-F "page_range=51" \
-F "page_range=100"
```

Esto reduce memoria y evita OOM. Mantener orden de concatenación.

## Health/version

```bash
# Interno (desde Hermes o contenedores en la misma network)
curl -s http://docling:5001/health
curl -s http://docling:5001/version

# Externo (desde fuera del VPS)
curl -sk https://docling.infinitek.pe/health
curl -sk https://docling.infinitek.pe/version
```

## Endpoints internos bloqueados

`/v1/memory/stats` puede devolver `403 Forbidden`; el contenedor está configurado para no exponer detalles internos de administración. No es error del servicio.
