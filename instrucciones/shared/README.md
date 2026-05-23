# Recursos compartidos (etapas C y D)

Parámetros, routing de modelos, catálogo de tools y patrones de delegación.

| Archivo | Propósito |
|---------|-----------|
| [agent_patterns.md](agent_patterns.md) | 10 reglas de delegación |
| [params.yaml](params.yaml) | Timeouts, batches, handoff budgets, `step_1_defaults` |
| [model_routing.yaml](model_routing.yaml) | Modelo por tarea + paths a schemas C/D |
| [catalog_modelos.md](catalog_modelos.md) | Capacidades de modelos |
| [catalog_tools.md](catalog_tools.md) | Search/fetch/parse + fallback |
| [formato_matriz_cumplimiento.md](formato_matriz_cumplimiento.md) | Formato matriz candidatos |

## Consumidores

- [C_conversion/00_orquestador.md](../C_conversion/00_orquestador.md)
- [D_portafolio/00_orquestador.md](../D_portafolio/00_orquestador.md)
- Scripts en `scripts/` y `proyecto/scripts/` que lean `params.yaml`

Etapa A: [A_pre_portafolio/free_reader_profiles.yaml](../A_pre_portafolio/free_reader_profiles.yaml) + config del portal.
