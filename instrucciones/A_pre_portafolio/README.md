# Etapa A — Pre-portafolio

Sistema **no agéntico**: portal, workers de ingesta, descarga, análisis rápido y chat de seguimiento.

**Estado:** SEACE operativo; otros canales y entrypoints planificados.

---

## Responsabilidad

| Función | Componente |
|---------|------------|
| Scan / detección | `apps/portal/seace_monitor/scanner.py` |
| Alta directa (entidad/cliente + referencia) | *planificado* — adapter por `source` |
| Creación manual | *planificado* — form + upload |
| Email / estudio de mercado | *planificado* — mailbox → paquete documental |
| Descarga documentos | `downloader.py`, Alfresco |
| Free reader | `analysis/fast_reader.py` |
| Chat post-análisis | `web/analysis_chat.py` |
| Estados hasta portafolio | `ProcessStatus` en BD |

---

## Free reader — perfiles por fuente

Ver **[free_reader_profiles.yaml](free_reader_profiles.yaml)**.

| Canal / ruta | Cronograma en prompt | Prompt |
|--------------|----------------------|--------|
| **SEACE** | No (ficha portal) | [prompts/seace_free_reader.md](prompts/seace_free_reader.md) |
| **Portales cliente** (AdP, Aeropuertos Andinos, etc.) | Sí, desde PDF cuando exista | [prompts/private_documents.template.md](prompts/private_documents.template.md) |
| **Email / market study** | Normalmente no existe; no forzar | Perfil planificado |
| **Manual** | Según UI | [prompts/manual.template.md](prompts/manual.template.md) (dinámico) |

La resolución final debe considerar `entity/source/workflow_profile/stage`; ver [docs/INPUT_SOURCES.md](../../docs/INPUT_SOURCES.md).

### Portafolio sin analizar

Desde **`descargada`**, marcar **`portafolio`** dispara free reader con perfil del item si falta resumen y cataloga el item como `interest_status=opportunity`.

---

## Prompts

| Archivo | Uso |
|---------|-----|
| [prompts/seace_free_reader.md](prompts/seace_free_reader.md) | Análisis multi-PDF SEACE |
| [prompts/seace_followup.md](prompts/seace_followup.md) | Chat de seguimiento |
| [prompts/private_documents.template.md](prompts/private_documents.template.md) | Perfil portales privados |
| [prompts/manual.template.md](prompts/manual.template.md) | Perfil manual dinámico |

Código portal:

- `fast_reader.py` → perfil desde `free_reader_profiles.yaml`
- `gemini_session.py` → `instrucciones/A_pre_portafolio/prompts/seace_followup.md`

---

## Layout en disco (objetivo)

```
pre_portafolio/
  documentos/
  fast_analysis/profile.json
  free_reader_summary.md
```

Ver [docs/STAGES.md](../../docs/STAGES.md).

---

## Referencias

- [docs/STAGES.md](../../docs/STAGES.md)
- [docs/INPUT_SOURCES.md](../../docs/INPUT_SOURCES.md)
- [vision/flujo_completo.md](../vision/flujo_completo.md)
