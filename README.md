# tender_procurement

Workflow basado en agentes LLM para armar shortlists de equipamiento en respuesta a licitaciones, partiendo de EETT (Especificaciones Técnicas) y aclaraciones, hasta llegar a un consolidado auditable con matrices de cumplimiento por candidato.

## Estado

**v0.1** — Primera versión completa de la documentación del workflow, lista para iterar. No ejecutada todavía contra una licitación real. Ver `REVIEW_FRESH_EYES.md` para crítica honesta del diseño y las 7 cosas a corregir antes de un primer run real.

## Estructura

```
tender_procurement/
├── README.md                       ← este archivo
├── REVIEW_FRESH_EYES.md            ← crítica del diseño v0.1
├── MEJORAS_PROPUESTAS.md           ← backlog de mejoras propuestas
├── instrucciones/                  ← workflow ejecutable
│   ├── README.md                       (mapa de la carpeta)
│   ├── 00_prompt_orquestador.md        (punto de entrada del orquestador)
│   ├── 01_workflow.md                  (runbook operativo de los 7 pasos)
│   ├── params.yaml                     (timeouts, batches, combos modelo+tool)
│   ├── catalog_modelos.md              (modelos disponibles + funciones permitidas)
│   ├── catalog_tools.md                (search/fetch providers)
│   ├── formato_matriz_cumplimiento.md  (formato obligatorio matriz por candidato)
│   ├── prompts/                        (13 plantillas de subagentes)
│   └── schemas/                        (4 contratos JSON canónicos)
└── debate y entregables antiguos/  ← histórico, conservado para referencia
```

## Cómo se usa

1. Apuntar un agente orquestador (GPT-5.5, Gemini 3.1 Pro o Kimi K2.6) a la carpeta `instrucciones/`.
2. Indicarle dónde está la carpeta del proyecto con los EETT/anexos/aclaraciones de la licitación.
3. El orquestador lee `00_prompt_orquestador.md` y desde ahí descubre el resto.

Plataformas previstas: OpenClaw, Hermes Agent.

## Pendientes prioritarios (de `REVIEW_FRESH_EYES.md`)

1. Prompts dedicados para consolidaciones 2.2 y 2.4
2. Batching del revisor de specs (paso 3.2)
3. Enforce orden topológico padres→hijos en paso 3.1
4. Diferenciar `PARCIAL` en tres subtipos
5. Mover Gate de revisión humana antes del paso 6
6. Codificar reglas de selección de modelos en `model_routing.yaml`
7. Calibration run con licitación pasada antes de producción
