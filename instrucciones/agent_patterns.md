# Patrones de delegación, orquestación y sub-agentes

> Referencia normativa que el orquestador debe leer al arrancar. Define cómo delegar trabajo a LLMs/agentes y cómo manejar las transiciones entre pasos. Adoptado tras la ejecución de ICAO-00068 (mayo 2026) que reveló problemas sistémicos de delegación.

Aplica al workflow de procurement: documentos EETT/aclaraciones → BOM → búsqueda → consolidado.

---

## 1. Marco conceptual

Hay **tres niveles** que conviene distinguir antes de delegar nada. Confundirlos es el origen del 80% de los problemas en la corrida de ICAO-00068.

**LLM call (one-shot)**: una sola llamada al modelo con un prompt y un output. Sin loop, sin herramientas, o con herramientas pero sin autonomía para decidir cuándo parar. **La mayoría de los pasos de este workflow son esto**, no agentes.

**Workflow**: una secuencia de LLM calls orquestada por código determinístico. Anthropic: "Workflows are systems where LLMs and tools are orchestrated through predefined code paths". El control de flujo está en el código del orquestador, no en el modelo.

**Agent**: un LLM que opera en un loop, decide qué herramientas usar, evalúa resultados, y elige cuándo terminar. El control de flujo está en el modelo. Solo se justifica para tareas genuinamente exploratorias.

**Regla**: usar el nivel más bajo de complejidad que cumpla los requisitos. Multi-agent paralelo consume ~15× más tokens que un chat y ~4× más que un single agent — solo conviene cuando la tarea es genuinamente paralelizable y el valor del output paga ese costo.

---

## 2. Mapa de patrones aplicables

### 2.1. Prompt chaining (sequential pipeline)

**Qué es**: descomposición en subtareas fijas; cada LLM call procesa el output de la anterior; gates programáticos entre etapas.

**Anti-pattern — Orchestrator-as-god**: un LLM planner que decide en cada paso a quién llamar manteniendo todo el estado en su context. Genera latency stacking, bottleneck, y "un mal token en su context envenena el resto de la corrida".

**Aplica a**: casi todo este workflow. Pasos 1.4, 2, 3, 4, 7 son cadenas determinísticas con gates programáticos.

---

### 2.2. Evaluator-optimizer (producer + critic loop) — bounded

**Qué es**: una LLM call genera, otra evalúa, con criterios claros. Anthropic: "particularly effective when we have clear evaluation criteria, and when iterative refinement provides measurable value".

**Anti-pattern — Loop sin handoff budget**: dos agentes iterando hasta quemarse el presupuesto porque ninguno sabe cuándo está "done". La solución no es un mejor prompt: es un **handoff budget explícito** (típicamente 1, raramente 2) y la regla "no hay reverse edge": si el revisor encuentra problemas mayores, falla loud y escala al humano, no devuelve al productor para "otra iteración".

**Aplica a**: Paso 1.4 (ejecutor + auditor de merge), Paso 3.2 (revisor "ojos frescos" de specs), Paso 7.2 (QA final).

---

### 2.3. Orchestrator-workers con fan-out paralelo

**Qué es**: un LLM lead descompone tareas independientes, las despacha a workers paralelos con context windows propios, y sintetiza. Cada worker tiene "distinct tools, prompts, and exploration trajectories—que reduce path dependency y permite investigaciones independientes y exhaustivas".

**Anti-pattern — Every-agent-can-call-every-agent (mesh)**: sin un único punto de handoff, sin enforcement de budget, los loops son inevitables.

**Tradeoff**: Anthropic reporta +90% sobre single-agent en tareas de investigación, pero consume ~15× más tokens. Solo se justifica cuando "el valor de la tarea es suficientemente alto para pagar la mejora de performance" y la tarea es **genuinamente paralelizable** (subtareas independientes que no comparten contexto crítico).

**Aplica a**: **únicamente Paso 6** (búsqueda de candidatos por ítem). Un item-manager por ítem que despacha N search-workers en paralelo, cada uno con su propio context window y tool budget.

---

### 2.4. Hierarchical task decomposition con ownership completo

**Qué es**: niveles —planner que decompone pero no escribe el output, sub-planners que dividen, workers aislados que ejecutan. Cursor/Anthropic convergieron en esto tras encontrar que estructuras planas fallan a escala: "veinte agentes desaceleraron al throughput de dos o tres… las estructuras planas inducen evitación de responsabilidad".

**Anti-pattern — Integrator central como QA gate**: Cursor lo intentó y lo eliminó: "se volvió un bottleneck obvio. Cientos de workers y una sola compuerta".

**Aplica a**: estructura general. El orquestador es el planner; los prompts de cada paso son los workers; cada uno tiene un único entregable claro.

---

### 2.5. Handoff with ownership boundaries

**Qué es**: estado tipado compartido con un campo `owner` que indica quién es el único que puede escribir; el handoff es un function call con payload, no un tool call al otro agente; el handoff cuenta contra un budget que **falla loud** si se excede.

**Anti-pattern crítico — Context-as-content**: pasar el contenido del archivo en el campo `context` cuando el contrato esperaba paths o handles. **Esto fue exactamente el error del INC-001 en ICAO-00068**, que generó timeouts artificiales y motivó toda la arquitectura paralela de scripts Python + API directa.

**Regla operativa explícita**: `context` lleva **ubicaciones, identificadores y referencias**, no payload. El sub-agente lee los archivos por su cuenta usando sus tools (`read_file`, `terminal`, etc.).

**Aplica a**: cualquier transición entre pasos. Cuando el orquestador delega, pasa paths a inputs, paths a outputs esperados, ruta al prompt-template, ruta al schema de salida. No pasa contenido.

---

### 2.6. Context engineering: share full traces, not just messages

**Qué es**: Cognition formaliza dos principios casi inviolables:
1. **Share context, share full agent traces, not just individual messages**.
2. **Actions carry implicit decisions, and conflicting decisions carry bad results**.

Ejemplo canónico: dos sub-agentes que generan el background y el pájaro de un Flappy Bird, cada uno con asunciones implícitas distintas sobre estilo, y un final agent que tiene que componer dos piezas incoherentes.

**Anti-pattern — Sub-agentes a los que se les pasa solo su subtarea**, no el contexto general ni las decisiones previas. Outputs inconsistentes que no componen.

**Aplica a**: **explica el INC-006 de ICAO-00068** (28 requisitos FALTANTE detectados post-hoc). Cada variante del BOM exploded decidió implícitamente cosas distintas sobre naming y agrupación; al consolidar, los requisitos que no encajaban con las otras decisiones se perdieron. La solución no es revisor más fuerte: es un **scratchpad de decisiones compartido** entre pasos (decisiones de naming, abreviaturas, supuestos).

---

### 2.7. Context engineering: write / select / compress / isolate

**Qué es**: cuatro estrategias para manejar context window finito:
- **Write**: scratchpads, memorias fuera del context window.
- **Select**: RAG, recuperar solo lo relevante para la subtarea.
- **Compress**: resumir tras N turnos.
- **Isolate**: sub-agentes con context window propio.

Anthropic refuerza: "context, therefore, must be treated as a finite resource with diminishing marginal returns… context rot: as the number of tokens in the context window increases, the model's ability to accurately recall information from that context decreases".

**Anti-pattern — Context dump**: meter todo el documento de 200 páginas en el prompt "por si acaso". Genera context rot, sube costo, degrada precisión.

**Aplica a**: pasos 2 y 3. El BOM y las specs no necesitan ver el pliego entero, sino las secciones relevantes seleccionadas. El scratchpad explícito entre pasos es la implementación práctica de "write context".

---

### 2.8. Constraints over instructions

**Qué es**: decirle al modelo **qué NO hacer** funciona mejor que decirle qué SÍ hacer. Mejor aún: hacer los constraints **ambientales** (físicos) en lugar de instruccionales. Si el agente no tiene acceso a internet, no necesitás decirle que no la use.

**Anti-pattern — Checkbox mentality**: listas de tareas prescriptivas que el modelo completa literalmente sin entender el intent.

**Aplica a**: todos los pasos. En lugar de listas "asegurate de incluir X, Y, Z", restringir el JSON schema de output a un shape donde X, Y, Z son `required`. La validación post-call hace el rol de "constraint físico" que el modelo no puede esquivar.

---

### 2.9. Structured output validation post-call (gate primitive)

**Qué es**: cada LLM call devuelve JSON contra schema; un validador determinístico (no otro LLM) verifica. Si falla, retry con el error como feedback o falla loud.

**Anti-pattern — Free-text output más parser regex**: frágil, no falla loud, los errores de schema se acumulan silenciosamente downstream. El "bracket counting rescue" implementado en ICAO-00068 es un parche sobre este problema, no la solución correcta.

**Aplica a**: salida de todos los pasos. Particularmente crítica para Paso 2 (BOM) y Paso 3 (specs): un faltante de campo es un BOM con requisitos perdidos. El validador detecta inmediatamente que `cantidad`, `unidad` o `referencia_sección` está ausente.

---

### 2.10. Tool budget explícito (en lugar de max_tokens)

**Qué es**: en lugar de un límite de tokens (que el modelo no percibe como restricción accionable), un presupuesto de tool calls que el modelo conoce y administra. El paper BATS (2025): "standard agents lack inherent budget awareness and without explicit signals, they often perform shallow searches and fail to utilize additional resources".

Complemento: **loop primitive** en tareas largas — un loop explícito de "trabajar → verificar progreso → continuar o salir" en lugar de un solo turno largo.

**Anti-pattern — `max_tokens` alto y esperar autogobierno**: sin señal explícita, los modelos hacen búsquedas superficiales o truncan output sin avisar. **Esto fue exactamente el INC-002 de ICAO-00068**.

**Aplica a**: Paso 6 (search workers) — cada worker recibe budget de N búsquedas y M páginas; cuando se agota, devuelve lo que tiene. Y todo paso con output estructurado grande (Paso 2, Paso 3): si el modelo se queda sin output, el harness debe rechazar y pedir retry, no rescatar bracket counting.

---

## 3. Reglas operativas del orquestador

Diez reglas no negociables, destiladas de los patrones anteriores. El orquestador debe respetarlas en cada delegación.

1. **`context` lleva ubicaciones, no contenido**. Paths, doc IDs, range references. Nunca el texto del archivo.
2. **Cada LLM call devuelve JSON validado contra schema**; falla bloquea avance.
3. **Tool budget explícito por delegación**, no `max_tokens`. El sub-agente debe percibirlo como restricción accionable.
4. **Handoff count budget global**. Cruzarlo es falla loud, no retry silencioso.
5. **Un único `owner` por estado en cada momento**. El campo `owner` viaja con el payload; el receptor verifica `assert state.owner == self`.
6. **El planner no produce el output final**. Quien decompone no es quien escribe.
7. **Trazas completas, no mensajes aislados**, cuando hay sub-agentes en paralelo. Compartir decisiones implícitas previas.
8. **Logging en el handoff**, no en cada turno. El span de boundary es la unidad de observabilidad más informativa.
9. **Falla loud antes que retry silencioso**. Un sub-agente que no puede cumplir su definition of done debe romper la corrida.
10. **Model routing por evidencia, no por reputación general**. Ver `model_routing.yaml`. Anthropic mostró que upgrade de modelo > duplicar token budget — la elección de modelo es variable independiente con efectos no obvios.

---

## 4. Cuándo SÍ y cuándo NO multi-agent

**Cognition** (perspectiva crítica): la mayoría de los workflows mal llamados "multi-agent" se resuelven mejor con un single-threaded linear agent — o, en términos de Anthropic, prompt chaining workflow. Los multi-agent paralelos rompen el principio de contexto compartido y generan inconsistencias entre piezas que después hay que componer.

**Anthropic** (perspectiva pro): para tareas genuinamente paralelizables —búsqueda breadth-first, exploración de espacios independientes— multi-agent es vital. El factor que predice 80% de la varianza de performance es el uso de tokens.

**Síntesis aplicada a este workflow**: multi-agent se justifica solo cuando las subtareas son genuinamente independientes Y el valor del output paga el 15× de tokens. En este workflow, eso aplica **únicamente al Paso 6 (búsqueda de candidatos por ítem)**. El resto es prompt chaining + evaluator-optimizer + gates programáticos.

**Decisión post-ICAO-00068 sobre las variantes paralelas del Paso 2**: las 3 variantes del BOM HL y las 4 del BOM Exploded eran sobre-arquitectura. Cognition tenía razón en este caso: un solo productor + auditor "ojos frescos" da mejor resultado que múltiples variantes que cada una decide implícitamente cosas distintas y después hay que consolidar. Las variantes consumen 7× tokens, generan inconsistencias en naming/agrupación (cada modelo decide diferente), y la consolidación posterior pierde requisitos que no encajan en el "consenso". **Se eliminan en v0.2**.

---

## 5. Aplicación a cada paso del workflow

### Paso 1 — Normalización documental
**Tipo**: workflow no-LLM determinístico + LLM call selectiva para visión.
**Patrones**: Constraints over instructions (schema de output del extractor define required fields).
**Flujo correcto** (post-ICAO):
1. Si DOCX → convertir a PDF (determinístico).
2. PDF → pasar por optimizador (`pdf_image_audit.py`: quita headers/footers/firmas/sellos/decorativos).
3. PDF optimizado → LandingAI ADE → markdown.

### Paso 1.2b — Planos/diagramas pre-OCR
**Tipo**: workflow no-LLM determinístico + LLM call visual selectiva.
**Patrones**: 2.1 (chaining), 2.7 (select), 2.9 (schema validation), 2.10 (tool budget).
**Reglas**:
- Detector geométrico solo propone páginas candidatas; no decide eliminación.
- Modelo visual confirma plano/diagrama y extrae `identifier_or_title` visible.
- Páginas confirmadas se extraen a PDF aparte y se sustituyen por resumen textual en `{stem}_preocr.pdf`.
- Páginas grandes que son tablas/anexos textuales quedan en OCR normal.

### Paso 1.4 — Merge aclaraciones (ejecutor + auditor)
**Tipo**: evaluator-optimizer bounded.
**Patrones**: 2.2 (evaluator-optimizer), 2.5 (handoff), 2.9 (schema validation).
**Reglas**:
- Handoff budget = 1 (auditor revisa una vez).
- No hay reverse edge: si auditor encuentra problemas mayores, falla loud y escala al humano.
- Context para auditor: documento original + diff propuesto, NO solo el documento ya mergeado.

### Paso 1.5 — Índice estructural de Markdown
**Tipo**: prompt chaining / LLM call por documento, con lectura completa secuencial.
**Patrones**: 2.1 (chaining), 2.7 (write/select), 2.9 (schema validation), 2.10 (tool budget explícito).
**Reglas**:
- Leer TODO el Markdown por ventanas de 200 líneas con overlap de 50.
- No confiar ciegamente en headings Markdown; reconstruir jerarquía real con señales combinadas.
- No extraer BOM ni entregables en esta pasada.
- El indexador puede sugerir correcciones Markdown estructurales de bajo riesgo, pero NO modifica el archivo fuente.
- Outputs planos: `artifacts/step_1_index/{stem_original}_index.json/.md`.
- JSON contra `schemas/document_index.schema.json`.

### Paso 2 — BOM HL + Exploded
**Tipo**: prompt chaining single-shot, sin variantes.
**Patrones**: 2.1 (chaining), 2.6 (trazas completas / scratchpad), 2.7 (write/select), 2.9 (schema validation).
**Cambio v0.2**: **eliminar variantes paralelas**. Reemplazar por:
- 2.1 BOM HL: 1 productor + 1 auditor "ojos frescos" (modelo distinto).
- 2.3 BOM Exploded: 1 productor + 1 auditor "ojos frescos" (modelo distinto).
- Scratchpad explícito de decisiones (naming, abreviaturas, supuestos) compartido entre 2.1 y 2.3.

### Paso 3 — Specs + herencia + revisor
**Tipo**: prompt chaining + evaluator-optimizer.
**Patrones**: 2.1, 2.2, 2.6, 2.7, 2.9.
**Reglas**:
- 3.1 productor procesa en batches con context selecto (solo secciones relevantes por ítem, no documento completo).
- 3.2 revisor: context fresco — no ve el razonamiento de 3.1, solo el output y el documento fuente. Handoff budget = 1.

### Paso 4 — BOM búsqueda
**Tipo**: workflow no-LLM determinístico.
**Patrón**: ninguno LLM-agentic.
Transforma el BOM consolidado en queries estructuradas. Si hay LLM call, es one-shot de reformulación.

### Paso 6 — Búsqueda de candidatos
**Tipo**: **orchestrator-workers con fan-out paralelo. ÚNICO paso multi-agent del workflow**.
**Patrones**: 2.3 (orchestrator-workers), 2.4 (hierarchical), 2.5 (handoff), 2.7 (isolate), 2.8 (constraints), 2.10 (tool budget).
**Estructura**:
- Item-manager (1 por ítem): agente real con loop, tools, autonomía bounded.
- 2 search-workers por ítem en paralelo: cada uno context window propio, tool budget explícito (N búsquedas, M páginas, K fetches de PDF).
- Búsqueda en español Y en inglés.
- Capacidad de descargar y parsear datasheet PDF del fabricante.
- Schema de output con evidencia citada (URL + página + sección).
**Matriz de cumplimiento**: producida **separadamente** como LLM call adicional post-búsqueda, con context = item-spec + candidato-evidencia. No es responsabilidad del search worker.

### Paso 7 — Consolidación + QA
**Tipo**: prompt chaining + critic terminal.
**Patrones**: 2.1, 2.2 (bounded, sin reverse edge), 2.9.
**Reglas**:
- 7.1 Consolidador: LLM call con context engineering serio (matriz Paso 6 + specs Paso 3 + BOM Paso 2).
- 7.2 QA: critic terminal. Si encuentra inconsistencias graves, **falla loud y escala**, no devuelve al consolidador.

---

## 6. Decisiones de ICAO-00068 que se invierten en v0.2

| Decisión v0.1 | Causa raíz observada | Decisión v0.2 |
|---|---|---|
| 3 variantes BOM HL + consolidación | INC-006: 28 requisitos FALTANTE por decisiones implícitas inconsistentes entre variantes | 1 productor + 1 auditor "ojos frescos" |
| 4 variantes BOM Exploded + consolidación | Idem + INC-004 (504 timeout por 426 items en 1 prompt) | 1 productor + 1 auditor + scratchpad de decisiones compartido con 2.1 |
| Búsqueda solo en español (Paso 6 v1) | INC-007: 0% hit rate | Búsqueda en español Y en inglés |
| Solo Firecrawl para fetch | INC-009: créditos agotados, sin alternativa | Pool de fetch tools con fallback (ver `catalog_tools.md`) |
| Sin acceso a spec sheets PDF | Hit rate 10.7% | Búsqueda explícita de PDFs de fabricante + parser |
| max_tokens alto, sin tool budget | INC-002: kimi/deepseek/minimax truncaron JSON | Tool budget explícito + schema validation |
| Selección de modelo por reputación | glm-5p1 fue el único confiable para JSON; los demás truncaron | `model_routing.yaml` por evidencia |

---

## Fuentes

- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic — Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Cognition — Don't build multi-agents](https://cognition.ai/blog/dont-build-multi-agents)
- [Anhaia — Multi-agent handoff with ownership boundaries](https://dev.to/gabrielanhaia/multi-agent-handoff-with-ownership-boundaries-nobody-crosses-nll)
- [Lavaee — Five primitives of agent swarms](https://alexlavaee.me/blog/five-primitives-agent-swarms/)
- [GitHub — Building reliable AI workflows with agentic primitives](https://github.blog/ai-and-ml/github-copilot/how-to-build-reliable-ai-workflows-with-agentic-primitives-and-context-engineering/)
- [LangChain — Context engineering for agents](https://blog.langchain.com/context-engineering-for-agents/)
- [Microsoft Azure — AI Agent Orchestration Design Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [OpenAI — A Practical Guide to Building Agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- [BATS — Budget-Aware Tool-Use Enables Effective Agent Scaling (arXiv 2025)](https://arxiv.org/html/2511.17006v1)
