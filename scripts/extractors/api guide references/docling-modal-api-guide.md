# Docling Modal API — Guía de uso para agentes

**Endpoint base:** `https://rmichelena--docling-converter-fastapi-app.modal.run`

Servicio serverless en Modal.com usando la **imagen oficial de docling-serve**. Misma API que el contenedor bare metal.

## Recomendación operativa

- **Siempre usar async** (`/v1/convert/file/async`). Modal tiene un timeout de 150s en web endpoints.
- Los containers escalan a cero cuando no se usan. Primera llamada tiene cold start (~30s extra).
- Si un task queda `pending` o `started` más de 10 minutos, el container fue preempted — reenviar.
- Para producción confiable, considerar `min_containers=1` (~$46/mo).

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

## Conversión asíncrona

### 1) Crear task

`POST /v1/convert/file/async`

```bash
curl -sS -X POST "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/convert/file/async" \
  -F "files=@/ruta/al/documento.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false"
```

Respuesta real:

```json
{
  "task_id": "8b43a5fc-cb22-4b8b-940d-63927106fcc1",
  "task_type": "convert",
  "task_status": "pending",
  "task_position": 1,
  "task_meta": null,
  "error_message": null
}
```

Importante: el campo es **`task_status`** (igual que el contenedor local).

### 2) Poll status

`GET /v1/status/poll/{task_id}`

Estados observados:

- `pending` — en cola
- `started` — procesando
- `success` — completado
- `failure` — falló (ver `error_message`)

```bash
curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/status/poll/$TASK_ID"
```

Respuesta real (pending):

```json
{
  "task_id": "8b43a5fc-cb22-4b8b-940d-63927106fcc1",
  "task_type": "convert",
  "task_status": "pending",
  "task_position": 1,
  "task_meta": null,
  "error_message": null
}
```

Respuesta real (started):

```json
{
  "task_id": "8b43a5fc-cb22-4b8b-940d-63927106fcc1",
  "task_type": "convert",
  "task_status": "started",
  "task_position": null,
  "task_meta": null,
  "error_message": null
}
```

Respuesta real (success):

```json
{
  "task_id": "8b43a5fc-cb22-4b8b-940d-63927106fcc1",
  "task_type": "convert",
  "task_status": "success",
  "task_position": null,
  "task_meta": null,
  "error_message": null
}
```

Poll cada 10-15s hasta que `task_status` sea `success` o `failure`.

### 3) Obtener resultado

Solo cuando `task_status == "success"`:

`GET /v1/result/{task_id}`

```bash
curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/result/$TASK_ID" \
  -o resultado.json
```

Respuesta real (JSON):

```json
{
  "document": {
    "filename": "EXPEDIENTE TECNICO ADP-PIE-SPAA-23011-L-E-ETE-001_rev.B_preocr.pdf",
    "md_content": "|           | ... | EXPEDIENTE TÉCNICO ...",
    "json_content": "...",
    "html_content": "...",
    "text_content": "...",
    "doctags_content": "..."
  },
  "status": "success",
  "errors": [],
  "processing_time": 128.27,
  "timings": {}
}
```

Extraer markdown desde `document.md_content`.

## Conversión sincrónica

### `POST /v1/convert/file`

Solo para documentos pequeños que completan en <150s (timeout de Modal).

```bash
curl -sS -m 150 -X POST "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/convert/file" \
  -F "files=@/ruta/al/documento.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false" \
  -o resultado.json
```

Respuesta: mismo formato que el resultado async (`document.md_content`).

## Health / version

```bash
curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/health"
curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/version"
```

Respuesta version:

```json
{
  "docling-serve": "1.18.0",
  "docling": "2.93.0",
  "docling-core": "2.74.1",
  "docling-ibm-models": "3.13.2",
  "docling-parse": "5.10.1",
  "python": "cpython-312 (3.12.12)"
}
```

## Ejemplo: workflow completo con curl

```bash
# 1. Start async conversion
TASK_ID=$(curl -sS -X POST "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/convert/file/async" \
  -F "files=@documento.pdf" \
  -F "image_export_mode=placeholder" \
  -F "include_images=false" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])")

# 2. Poll until done
while true; do
  STATUS=$(curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/status/poll/$TASK_ID" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['task_status'])")
  [ "$STATUS" != "pending" ] && [ "$STATUS" != "started" ] && break
  sleep 10
done

# 3. Fetch result
curl -sS "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/result/$TASK_ID" \
  -o resultado.json

# 4. Extract markdown
python3 -c "import json; d=json.load(open('resultado.json')); print(d['document']['md_content'])" > documento.md
```

## Ejemplo: integración n8n / Hermes (HTTP Request node)

**Paso 1 — Crear task:**

```json
{
  "method": "POST",
  "url": "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/convert/file/async",
  "sendBody": true,
  "bodyContentType": "multipart-form-data",
  "bodyParams": {
    "files": "={{ $binary.data }}",
    "image_export_mode": "placeholder",
    "include_images": "false"
  }
}
```

**Paso 2 — Poll status** (Wait + HTTP Request en loop):

```json
{
  "method": "GET",
  "url": "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/status/poll/{{ $json.task_id }}"
}
```

Condición de salida: `{{ $json.task_status !== 'pending' && $json.task_status !== 'started' }}`

**Paso 3 — Fetch resultado:**

```json
{
  "method": "GET",
  "url": "https://rmichelena--docling-converter-fastapi-app.modal.run/v1/result/{{ $json.task_id }}"
}
```

Markdown en `{{ $json.document.md_content }}`.

## Diferencias vs contenedor bare metal

| Aspecto | Bare Metal | Modal |
|---------|-----------|-------|
| URL | `http://docling:5001` / `https://docling.infinitek.pe` | `https://rmichelena--docling-converter-fastapi-app.modal.run` |
| API | Idéntica | Idéntica (misma imagen `docling-serve`) |
| 67-pág PDF | 58s | ~76s (warm), ~175s (cold) |
| RAM idle | 1.66 GB | 0 (scales to 0) |
| Always-on | Sí | No |
| Costo | Server fijo | $0.04/PDF |
| Preemption | No | Posible (spot instances) |

## Performance

- PDF 883 KB (20 págs): ~128s warm, ~175s cold
- PDF 2 MB (67 págs): ~76s warm, ~175s cold
- PDF 11 MB (135 págs): ~120s warm, ~300s cold (estimado)

Cold start incluye ~30s de carga de modelos. Todos los modelos (Docling + RapidOCR onnx) están baked en la imagen — no hay downloads en runtime.

Modal free tier: $30/month. Costo estimado ~$0.04/PDF (warm).

## Notas

- La imagen es `quay.io/docling-project/docling-serve:latest` + RapidOCR onnx models baked in
- Soporta los mismos parámetros que el contenedor local: `page_range`, `do_ocr`, `ocr_lang`, `table_mode`, etc.
- Los resultados async están en memoria — si el container se reinicia, el task desaparece y el poll devuelve `{"detail":"Task not found."}`
- Si un task queda `pending`/`started` >10 min, el container fue preempted — reenviar

## Management

```bash
# Deploy
cd /home/sysop/compose/docling-modal
source /home/sysop/.venv-modal/bin/activate
modal deploy docling_modal.py

# Test
modal run docling_modal.py --pdf /path/to/file.pdf

# Ver logs
modal app logs docling-converter

# Stop
modal app stop docling-converter -y
```

## Script

`/home/sysop/compose/docling-modal/docling_modal.py`
