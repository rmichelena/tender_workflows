# Google Document AI — Setup Guide

## 1. Habilitar la API

Google Cloud Console → APIs & Services → Library → **Cloud Document AI API** → Enable.

## 2. Crear Processor

1. Ir a **Document AI** → **Processors** → **Create Processor**
2. Tipo: **Layout Parser** (nombre: `test-layout-parser` o similar)
3. Anotar el **Processor ID** del URL (ej: `b8ea939312a8ff4`)

## 3. OAuth Token

Se necesita un token OAuth con scope `cloud-platform`. En Hermes Agent, esto se configura con:

```bash
$GSETUP --auth-url
# Seguir el flujo OAuth...
$GSETUP --auth-code "..."
```

El token se guarda en `google_token_personal.json`.

## 4. Service Agent (requerido para Batch/GCS)

El service agent permite que DocAI lea de GCS. Se crea una vez:

```bash
POST https://serviceusage.googleapis.com/v1beta1/projects/{PROJECT_NUMBER}/services/documentai.googleapis.com:generateServiceIdentity
Authorization: Bearer {OAUTH_TOKEN}
```

Retorna el email del service agent:
```
service-{PROJECT_NUMBER}@gcp-sa-prod-dai-core.iam.gserviceaccount.com
```

## 5. GCS Bucket

Crear bucket (ej: `hermoberto`):
- Location: misma que el processor (ej: `US`)
- Storage class: Standard

Otorgar permisos al service agent:
```
roles/storage.objectViewer
roles/storage.objectCreator
```

## 6. Verificación

```bash
# Test con doc pequeño
python3 docai_batch_gcs.py test.pdf /tmp/test_output/
```

## Troubleshooting

| Error | Causa | Fix |
|-------|-------|-----|
| `Failed to process all documents` | Service agent sin permisos en GCS | Crear service agent (paso 4) + otorgar permisos (paso 5) |
| `Service account does not exist` | Service agent no creado | Ejecutar `generateServiceIdentity` |
| 404 en processor | Processor ID incorrecto | Verificar en Console → Document AI → Processors |
| Token expired | OAuth token vencido | Refrescar con `$GSETUP --auth-url` |
| 429 Rate limit | Demasiados requests | Batch mode ya maneja esto; reducir concurrency si es necesario |

## Límites del API

- **Online** (`:process`): máx 15 páginas, respuesta inmediata
- **Batch** (`:batchProcess`): máx 500 páginas, async con polling
- **Free tier**: 1000 páginas/mes
- **Rate limits**: 1200 requests/min (online), 10 operaciones concurrentes (batch)
