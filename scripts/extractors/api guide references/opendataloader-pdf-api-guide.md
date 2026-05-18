# OpenDataLoader PDF Markdown API

Servicio local para convertir PDFs a Markdown limpio usando OpenDataLoader PDF con backend hybrid Docling/OCR.

## Endpoints

### Health

Interno desde Docker/Traefik network:

```http
GET http://opendataloader-pdf-md:5003/health
```

Respuesta:

```json
{"status":"ok","hybrid_url":"http://opendataloader-pdf-hybrid:5002"}
```

### Convertir PDF a Markdown

```http
POST http://opendataloader-pdf-md:5003/convert/markdown
Content-Type: multipart/form-data
```

Campos:

- `file`: PDF a convertir. Requerido.
- `hybrid`: backend OpenDataLoader. Default: `docling-fast`.
- `hybrid_mode`: `auto` o `full`. Default: `auto`.
- `hybrid_timeout_ms`: timeout al backend en ms. Default: `0` sin timeout.
- `pages`: rango opcional, por ejemplo `1,3,5-7`.
- `fallback`: `true/false`. Default: `true`.

Ejemplo curl:

```bash
curl -sS -X POST \
  http://opendataloader-pdf-md:5003/convert/markdown \
  -F 'file=@documento.pdf' \
  -F 'hybrid_mode=auto' \
  -o documento.md
```

## Arquitectura

- `opendataloader-pdf-md:5003`: wrapper HTTP propio. Ejecuta el CLI `opendataloader-pdf` y devuelve Markdown.
- `opendataloader-pdf-hybrid:5002`: backend oficial hybrid de OpenDataLoader/Docling. Hace OCR/ML y devuelve JSON DoclingDocument al CLI.

El hybrid **no elimina Markdown** en OpenDataLoader. Solo que el servidor hybrid oficial no expone endpoint Markdown directo; Markdown lo genera el cliente/CLI.

## Salida Markdown

El wrapper fuerza:

```bash
--format markdown
--image-output off
--hybrid docling-fast
```

Así evita imágenes embebidas/base64 y devuelve Markdown limpio para RAG/n8n/Hermes.

## Estado probado

- Health interno desde Hermes: OK.
- Conversión PDF 215 KB: OK, ~16.8 s.
- RAM observada:
  - `opendataloader-pdf-hybrid`: ~1.2–1.5 GB.
  - `opendataloader-pdf-md`: ~33 MB idle/después de conversión.

## Notas

- `https://opendataloader.infinitek.pe` requiere DNS apuntando al Traefik host. Al momento de esta guía el hostname no resolvía desde el servidor.
- Para PDFs escaneados pesados, usar `hybrid_mode=auto` primero. `full` manda todas las páginas al backend y puede consumir más tiempo/RAM.
