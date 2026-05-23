# Instrucciones — mapa de etapas A→D

Runbooks, prompts, schemas y config compartida del producto **tender_workflows**.

**Visión global:** [vision/flujo_completo.md](vision/flujo_completo.md) · **Canónico:** [docs/STAGES.md](../docs/STAGES.md)

---

## Por dónde empezar

| Rol | Leer |
|-----|------|
| Portal / ingesta | [A_pre_portafolio/README.md](A_pre_portafolio/README.md) |
| UI staging portafolio | [B_staging_portafolio/README.md](B_staging_portafolio/README.md) |
| Pipeline documental | [C_conversion/README.md](C_conversion/README.md) |
| Agente procurement | [D_portafolio/README.md](D_portafolio/README.md) |
| Delegación LLM | [shared/agent_patterns.md](shared/agent_patterns.md) |

---

## Estructura

```
instrucciones/
├── vision/flujo_completo.md
├── shared/                         # params, routing, tools, agent_patterns
├── A_pre_portafolio/prompts/
├── B_staging_portafolio/schemas/
├── C_conversion/prompts/ + schemas/
├── D_portafolio/prompts/ + schemas/ + payloads/thematic_axes/
├── prompts/README.md               # puntero legacy
├── schemas/README.md               # puntero legacy
├── 00_prompt_orquestador.md        # ⚠ DEPRECADO
└── 01_workflow.md                  # ⚠ DEPRECADO (detalle hasta migrar)
```

---

## Legacy

| Archivo | Reemplazo |
|---------|-----------|
| `00_prompt_orquestador.md` | `C_conversion/00_orquestador.md` + `D_portafolio/00_orquestador.md` |
| `01_workflow.md` Paso 1 | `C_conversion/01_runbook.md` |
| `01_workflow.md` Pasos 2–7 | `D_portafolio/01_runbook.md` |

---

## Convenciones

- **JSON canónico** en artifacts; MD/TSV/XLSX derivados por scripts u orquestador.
- **Context a subagentes:** paths, no contenido (`shared/agent_patterns.md`).
- Layout expediente: `pre_portafolio/` + `portafolio/` — ver [STAGES.md](../docs/STAGES.md).
