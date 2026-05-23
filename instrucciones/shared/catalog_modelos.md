# Catálogo de modelos disponibles

> El orquestador selecciona modelos desde este catálogo según la función requerida por cada paso.
> Reglas: diversidad en paralelo, rotación en reintentos, revisor ≠ productor.
> Ver `shared/params.yaml → model_pools` para los pools por función.

## Tabla de capacidades

| Modelo | Visión | Razonamiento | Contexto | Fortalezas principales |
|--------|--------|--------------|----------|-----------------------|
| GPT-5.5 | Sí | Muy fuerte | ~400K–1M | Planificación, orchestration, autonomía, tool use |
| GPT-5.4 | Sí | Fuerte | ~400K+ | Reasoning sólido, merge/consolidación |
| GPT-5.4 Mini | Sí | Medio | ~200K+ | Velocidad, throughput alto, costo bajo |
| GPT-5.3 Codex | Parcial | Fuerte (coding) | ~200K+ | Edición multiarchivo, scripts, validadores |
| GLM-5.1 | No | Fuerte | ~203K | Workflows autónomos, tool use, razonamiento estructurado |
| GLM-5V Turbo | Sí | Fuerte | ~203K | Visión documental, extracción de tablas/figuras |
| GLM-5 Turbo | No | Medio | ~203K | Throughput rápido, tareas repetitivas |
| Gemini 2.5 Pro | Sí | Muy fuerte | ~1M | Docs largos, multimodalidad, auditoría, contexto masivo |
| Gemini 3.1 Pro | Sí | Muy fuerte | ~1M+ | Contexto extremo, tool calling nativo |
| Kimi K2.6 | Sí | Muy fuerte | ~256K | Agentes, tool orchestration, búsqueda exploratoria |
| Kimi K2.5 Turbo | Sí | Moderado | ~256K | Eficiente, workflows largos, buen tool use |
| DeepSeek V4 Pro | No | Muy fuerte | ~256K | Razonamiento técnico avanzado, coding |
| Qwen 3.6 Plus | Limitado | Fuerte | ~256K | Reasoning, tool use, diversidad |

## Funciones permitidas por modelo

| Función | Modelos aptos |
|---------|---------------|
| Orquestador | GPT-5.5, Gemini 3.1 Pro, Kimi K2.6 |
| OCR / Visión documental | Gemini 2.5 Pro, GPT-5.5, GLM-5V Turbo, Kimi K2.6 |
| BOM / Extracción (variantes) | Kimi K2.6, GLM-5.1, Gemini 2.5 Pro, DeepSeek V4 Pro, Qwen 3.6 Plus, GPT-5.4 Mini |
| Specs / Herencia (reasoning largo) | GPT-5.5, Gemini 2.5 Pro, GLM-5.1, Kimi K2.6 |
| Auditor / Revisor | GPT-5.4, Gemini 2.5 Pro, Kimi K2.6, GLM-5.1 |
| Subagente-Item (paso 6) | GPT-5.5, GLM-5.1, Kimi K2.6 |
| Search Worker (paso 6) | Kimi K2.5 Turbo, GPT-5.4 Mini, GLM-5 Turbo, Qwen 3.6 Plus |
| Throughput / Transformaciones | GPT-5.4 Mini, GLM-5 Turbo, Kimi K2.5 Turbo |

## Anti-patterns (no usar para)

| Modelo | No recomendado para |
|--------|--------------------|
| GPT-5.4 Mini | Auditoría crítica, decisiones de escalamiento |
| GLM-5.1, DeepSeek V4 Pro | OCR/visión (no tienen capacidad visual) |
| GLM-5 Turbo | Auditoría, decisiones complejas |
| GPT-5.3 Codex | Búsqueda web, OCR |
| Gemini 2.5/3.1 Pro | Workers repetitivos de alto throughput (sobredimensionado) |
| Kimi K2.5 Turbo | Auditoría crítica final |

## Reglas operativas

1. **Paralelo (mismo paso)**: modelos distintos entre subagentes paralelos.
2. **Revisor "ojos frescos"**: el modelo del revisor/auditor debe ser distinto al del productor.
3. **Paso 6**:
   - Subagente-item = modelo del pool `item_manager` (orquestación + razonamiento).
   - Workers = modelos del pool `search_worker` (más exploratorios y económicos), siempre dos modelos distintos.
4. **Rotación en reintentos**: no repetir la combinación modelo+tool ya usada en intentos previos para el mismo ítem.
