# Runbook — Etapa C (C.1 – C.4)

Resumen operativo. **Detalle línea por línea** sigue en `../01_workflow.md` (Paso 1) hasta completar migración.

---

## C.2 — Merge aclaraciones (ex 1.4)

| Rol | Prompt |
|-----|--------|
| Ejecutor | [prompts/merge_aclaraciones_ejecutor.md](prompts/merge_aclaraciones_ejecutor.md) |
| Auditor | [prompts/merge_aclaraciones_auditor.md](prompts/merge_aclaraciones_auditor.md) |

---

## C.3 — Eje 0 sobre MD (ex 1.3b) — opcional

**Prompt:** [prompts/axis0_free_reader.md](prompts/axis0_free_reader.md)  
**Output:** `portafolio/artifacts/step_1_axis0_preindex/axis0_go_no_go_summary.md`

---

## C.4 — Índice estructural (ex 1.5 – 1.5b)

**Prompt:** [prompts/document_indexer.md](prompts/document_indexer.md)  
**Schema:** `../schemas/document_index.schema.json`  
**Renderer:** `scripts/render_document_index_md.py`

---

## Handoff a etapa D

Ver [../D_portafolio/00_orquestador.md](../D_portafolio/00_orquestador.md).
