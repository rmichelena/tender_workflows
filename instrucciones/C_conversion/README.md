# Etapa C — Conversión / ingesta documental

Pipeline **híbrido**: pasos determinísticos (`scripts/`) con orquestador LLM solo ante fallos o subpasos visuales/semánticos acotados.

---

## Prompts

| Paso | Prompt |
|------|--------|
| C.1 / planos | [prompts/planos_vision.md](prompts/planos_vision.md) |
| C.1 fallback OCR | [prompts/ocr_vision.md](prompts/ocr_vision.md) |
| C.2 ejecutor | [prompts/merge_aclaraciones_ejecutor.md](prompts/merge_aclaraciones_ejecutor.md) |
| C.2 auditor | [prompts/merge_aclaraciones_auditor.md](prompts/merge_aclaraciones_auditor.md) |
| C.3 | [prompts/axis0_free_reader.md](prompts/axis0_free_reader.md) |
| C.4 | [prompts/document_indexer.md](prompts/document_indexer.md) |

## Schemas

| Schema | Uso |
|--------|-----|
| [schemas/plan_pages_analysis.schema.json](schemas/plan_pages_analysis.schema.json) | C.1.2b planos |
| [schemas/document_index.schema.json](schemas/document_index.schema.json) | C.4 índice |

Config: [shared/params.yaml](../shared/params.yaml), [shared/model_routing.yaml](../shared/model_routing.yaml).

Puente portal: `tender_bridge.py` → `prompts/axis0_free_reader.md`.

---

## Orquestador y runbook

- [00_orquestador.md](00_orquestador.md)
- [01_runbook.md](01_runbook.md)
