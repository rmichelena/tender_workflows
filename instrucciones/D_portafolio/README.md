# Etapa D — Trabajo en portafolio

Workflow **agéntico**: extracción temática, BOM, specs, búsqueda, consolidado.

---

## Prompts

```
D_portafolio/prompts/
  thematic/
    template.md          # render con payloads + render_thematic_prompt.py
    reader.md
    rendered/            # axis_* pre-renderizados
  bom/
    highlevel.md, exploded.md, auditor.md, para_busqueda.md, item_pack_from_bom.md
  specs/
    verificacion_herencia.md, revisor.md
  search/
    item_manager.md, search_worker.md, matriz_cumplimiento.md
  consolidation/
    paso7.md, QA_final.md, consolidator.md, axis1_crossdoc_consolidator.md
```

Payloads por eje: [payloads/thematic_axes/](payloads/thematic_axes/)

Schemas: [schemas/](schemas/) (temático, BOM, specs, candidatos, consolidado)

Renderizar prompt temático:

```bash
python3 scripts/render_thematic_prompt.py \
  --payload instrucciones/D_portafolio/payloads/thematic_axes/axis_4_goods_licenses_equipment.json \
  --output instrucciones/D_portafolio/prompts/thematic/rendered/axis_4_goods_licenses_equipment.md
```

---

## Orquestador y runbook

- [00_orquestador.md](00_orquestador.md)
- [01_runbook.md](01_runbook.md)

Scripts determinísticos: `proyecto/scripts/` (step4, step6, step7).
