# Mejoras propuestas al workflow de procurement

Este documento recopila propuestas de mejora **adicionales** a lo ya consensuado en la conversación. Está organizado por nivel de impacto y esfuerzo de implementación. Algunas son refinamientos pequeños; otras requieren decisión de diseño antes de incorporar.

---

## 1. Mejoras de robustez en la ejecución

### 1.1 `state.json` para reanudación de workflow

**Problema**: si el workflow se interrumpe en el paso 6 con 40 ítems ya procesados, no hay forma limpia de reanudar sin reejecutar todo.

**Propuesta**: el orquestador mantiene un `/proyecto/state.json` que registra, por paso/subpaso, el estado: `pending | in_progress | done | failed`, con timestamps y rutas a outputs producidos. Al arrancar, el orquestador lee `state.json` y reanuda desde el último paso `done`. El paso 6 además registra estado por ítem (no solo por paso global), para reanudar solo los ítems pendientes.

**Esfuerzo**: bajo. Una sección añadida al `00_prompt_orquestador.md` y al `01_workflow.md` define la mecánica.

### 1.2 Idempotencia explícita

**Problema**: si un subagente devuelve un resultado y luego se relanza accidentalmente, podría sobrescribir o duplicar.

**Propuesta**: cada subagente, antes de escribir su output, verifica si el archivo destino ya existe. Si existe y es un reintento intencional, mover el anterior a `_history/{timestamp}/`. Esto preserva auditoría y evita pérdida.

**Esfuerzo**: bajo. Regla en `00_prompt_orquestador.md`.

### 1.3 Hash de inputs para detectar cambios

**Problema**: si el humano edita un EETT después del paso 1, los pasos posteriores siguen consumiendo el archivo cambiado sin saberlo, perdiendo trazabilidad.

**Propuesta**: cada artefacto registra en su frontmatter el hash SHA-256 de los inputs que consumió. Al reanudar o re-ejecutar, el orquestador verifica si los hashes cambiaron y advierte que ciertos pasos requieren reproceso.

**Esfuerzo**: medio. Útil sobre todo si se planea reproceso parcial.

---

## 2. Mejoras en la calidad del output

### 2.1 Cross-check de candidatos entre ítems relacionados

**Problema**: si IT-0001 es una radio VHF y IT-0005 es su antena, los candidatos de IT-0005 deberían ser técnicamente compatibles con los candidatos de IT-0001 (frecuencia, conector, impedancia). Hoy esto se verifica indirectamente vía herencia, pero no se valida la compatibilidad **entre candidatos finales**.

**Propuesta**: agregar un sub-paso 7.0 (entre 6 y 7.1) llamado **"Compatibility Check"**: para cada relación parent-child, verificar que las combinaciones de candidatos (radio-A + antena-A, radio-A + antena-B, etc.) son compatibles. Producir un `compatibility_matrix.md` que el humano puede usar para armar bundles coherentes.

**Esfuerzo**: medio. Requiere un nuevo prompt y una pasada adicional.

### 2.2 Score numérico de candidatos para ranking

**Problema**: cuando hay 3 candidatos válidos, no es trivial elegir cuál proponer primero. La matriz tiene `OK/PARCIAL/NO_CUMPLE` pero no pondera por importancia del requisito.

**Propuesta**: agregar al esquema `candidato_cumplimiento.schema.json` un campo opcional `score_total` (0-100), calculado como % de OK ponderado por hard/soft. El orquestador puede ordenar el shortlist por score, dando al humano una jerarquía sugerida sin reemplazar su juicio.

**Esfuerzo**: bajo. Solo es agregar un cálculo al subagente-item.

### 2.3 Captura de variantes/familias

**Problema**: muchos fabricantes ofrecen el mismo producto en variantes (ej. CEIA HI-PE Plus Standard / Multi-Zone / Mil-Spec). Hoy el subagente-item propone una sola variante por fabricante; otras variantes válidas pueden quedar invisibles.

**Propuesta**: el `prompt_search_worker.md` instruye que, si el candidato pertenece a una familia con variantes, listar las variantes con un breve diferenciador. El subagente-item puede luego elegir la mejor variante o presentar las variantes top como "candidatos hermanos" en `notas`.

**Esfuerzo**: bajo. Refinar el prompt del worker.

### 2.4 Captura proactiva de "pricing tier" sin precio exacto

**Problema**: el workflow excluye precios deliberadamente. Pero saber si un candidato es "categoría premium" vs "categoría económica" ayuda a evitar mezclar ofertas de tier muy distinto.

**Propuesta**: el worker incluye un campo opcional `tier_estimado` (ENTRY / MID / PREMIUM / UNKNOWN) basado en señales públicas (posicionamiento del fabricante, certificaciones, complejidad). No precio exacto, solo categoría cualitativa.

**Esfuerzo**: bajo, pero requiere validar con el usuario si quiere esta señal.

---

## 3. Gobernanza, compliance y auditoría

### 3.1 Anonimización del proyecto en el log

**Problema**: el `decision_log.md` puede contener nombres de licitaciones, organismos compradores, productos que el cliente todavía no quiere divulgar.

**Propuesta**: el log usa un `project_alias` definido en `overlay_usuario.yaml`. Los nombres reales viven solo en `inputs/` y los outputs finales; el log puede compartirse para revisión sin filtrar el contexto comercial.

**Esfuerzo**: bajo. Solo es una convención al inicio.

### 3.2 Conflict-of-interest check en candidatos

**Problema**: si el cliente tiene relaciones comerciales con ciertos fabricantes (proveedor habitual, exclusividad, conflicto), debería poder vetarlos no solo por "marcas vetadas" genéricas sino con una nota razonada.

**Propuesta**: ampliar `overlay_usuario.yaml` con `relaciones_comerciales` (lista de marca + tipo de relación + acción: "preferir" / "vetar" / "informar"). El subagente-item aplica el filtro y registra la decisión.

**Esfuerzo**: bajo. Refinar overlay y prompt del subagente-item.

### 3.3 Versionado del consolidado

**Propuesta**: el consolidado final se versiona (`consolidado_v{N}.json`), con un `consolidado_changelog.md` que registra qué cambió entre versiones. Útil cuando se re-corren búsquedas con criterios actualizados o cuando se incorporan candidatos nuevos.

**Esfuerzo**: bajo.

---

## 4. Integraciones y extensibilidad

### 4.1 Plug-in de "compliance check" externo

**Propuesta**: al final del paso 6, opcionalmente invocar un servicio externo (CE database, FCC ID lookup, RoHS checker) para validar certificaciones que el datasheet declara. Formalizar como un "tool" adicional en `catalog_tools.md`.

**Esfuerzo**: medio. Requiere disponibilidad del tool en el runtime.

### 4.2 Exportación a sistemas downstream (ERP, e-procurement)

**Propuesta**: además del `consolidado.xlsx`, generar un `consolidado_erp.json` con la estructura mínima que requiere el ERP del cliente (campos custom, códigos internos, etc.). Definir el mapping en un `instrucciones/erp_mapping.yaml`.

**Esfuerzo**: medio, depende del ERP.

### 4.3 "Brief de respuesta a observaciones"

**Problema**: cuando se entrega el shortlist al equipo comercial y este pregunta "¿por qué no propusieron X?", responder requiere recorrer logs y resultados.

**Propuesta**: además del consolidado, generar un `proveedor_briefing.md` por proveedor que aparece en candidatos_descartados, con: motivo de descarte, ítem afectado, ronda en que se evaluó, y referencia al log. Genera trazabilidad reversa.

**Esfuerzo**: bajo. Una pasada de agregación tras paso 6.

---

## 5. Refinamientos al workflow actual

### 5.1 Gate humano opcional post-paso 4

**Propuesta**: actualmente Gate 5 confirma preferencias usuario. Sería bueno que **antes** de lanzar las búsquedas (paso 6) el humano vea el `BOM_busqueda.json` completo y pueda marcar ítems como "skip" (no buscar, ya tengo proveedor decidido) o "prioridad baja" (no urgente). Esto permite escalonar runtime y costos.

**Esfuerzo**: bajo. Añadir campo `accion_busqueda` al BOM búsqueda con valores `BUSCAR | SKIP | DIFERIR`.

### 5.2 Caching de búsquedas entre proyectos

**Problema**: muchos proyectos comparten ítems comunes (radios, fuentes, cables estándar). Re-buscar lo mismo en cada proyecto desperdicia tiempo.

**Propuesta**: el orquestador puede consultar (opcionalmente) una "biblioteca" persistente: si encuentra un ítem con specs equivalentes ya buscado en otro proyecto, reusar el shortlist como punto de partida (con re-validación de vigencia).

**Esfuerzo**: alto. Requiere infraestructura de caché y normalización de specs. Pero el ROI es alto si se procesan muchos proyectos similares.

### 5.3 Telemetría/métricas del workflow

**Propuesta**: el orquestador emite métricas al cerrar cada paso: tiempo total, tokens consumidos por modelo, reintentos por ítem, tasa de éxito en primer intento vs reintentos. Útil para optimizar combos modelo+tool en `params.yaml`.

**Esfuerzo**: bajo, alto valor para iteración.

### 5.4 Test-suite con un proyecto sintético

**Propuesta**: armar un `examples/proyecto_demo/` con EETT ficticias pero realistas (tres ítems: una radio, una antena hija, un servicio) + outputs esperados. Sirve para:
- Validar que un cambio de prompt no rompe el workflow.
- Onboarding rápido de nuevos proyectos.
- Benchmarking entre versiones del workflow.

**Esfuerzo**: medio. Es un esfuerzo único que paga dividendos a futuro.

---

## 6. Detalles de prompt-engineering

### 6.1 Instrucción explícita de "razoná antes de responder"

Para los pasos críticos (paso 3 verificación, paso 6 validación), agregar al prompt: *"Antes de producir el JSON final, escribí tu análisis paso a paso (razonamiento). Solo después produce el JSON. El razonamiento se descarta — solo se persiste el JSON, pero te ayuda a ser más preciso."*

Esto explota chain-of-thought sin contaminar los outputs.

### 6.2 Regla "cuando no estés seguro, marcá TBD"

En todos los prompts donde se extrae información, agregar explícitamente: *"Es preferible un `tbd` honesto que una invención. Si una información no está clara, no la inventes — agregala a `tbd[]` con descripción del problema."*

### 6.3 Few-shot examples por tipo de ítem

Los prompts actuales tienen ejemplos genéricos. Añadir 2-3 ejemplos por dominio común (telecom, energía, instrumentación, seguridad) ayuda al modelo a calibrar el nivel de granularidad esperado.

**Esfuerzo**: medio. Construir banco de ejemplos.

---

## 7. Mitigaciones de riesgos identificados durante la conversación

### 7.1 Riesgo: alucinación de URLs por workers de búsqueda

**Mitigación reforzada**: el subagente-item DEBE verificar con Firecrawl que cada URL devuelta por un worker es alcanzable y devuelve contenido coherente con lo afirmado, antes de incluir el candidato. URLs no verificadas → candidato descartado por "evidencia no verificable".

### 7.2 Riesgo: matriz incompleta por requisitos olvidados

**Mitigación**: el QA del paso 7 incluye un check específico: para una muestra de matrices, contar requisitos en la matriz vs requisitos en `ITEM-{id}_specs.json`. Si no coinciden → CRÍTICO.

### 7.3 Riesgo: sesgo de fuente única en validación

**Mitigación**: para requisitos hard cuantitativos críticos (ej. potencia, frecuencia), exigir al menos 2 fuentes distintas (datasheet + página de producto, o datasheet + manual de usuario). Si ambas son del fabricante pero coinciden, ✅. Si solo hay una, ⚠️ con nota "fuente única".

---

## 8. Sugerencias de UX para el humano

### 8.1 Dashboard ligero del estado del workflow

Un `state_dashboard.md` (o html) auto-generado en `/proyecto/` que muestra de forma visual: paso actual, % completado, ítems con estado VÁLIDO/CONDICIONADO/SIN_CANDIDATO, próximo gate humano. Útil cuando el workflow corre por horas y el humano vuelve a revisar.

### 8.2 "Diff de aclaraciones" como entregable visible

Después del paso 1.4, generar un `diff_aclaraciones.md` que muestre lado-a-lado el documento base y el aclarado, con highlighting de cambios. Reduce el costo cognitivo del Gate 2.

### 8.3 Resumen ejecutivo automático

Tras el paso 7, generar `executive_summary.md` (1-2 páginas, en castellano peruano neutral) que resuma: total de ítems, % resueltos, top 3 hallazgos, ítems que requieren decisión humana, próximos pasos sugeridos. Pensado para enviar a stakeholders no técnicos.

---

## 9. OCR/normalización: arquitectura híbrida en lugar de LLM-vision puro

**Problema**: el paso 1.1–1.3 actual usa un único subagente con un LLM de visión (Gemini 2.5 Pro / GPT-5.5 / Kimi K2.6) y, en modo "complejo", duplica con diff. Esto tiene un defecto sistémico que no se detecta con el diff actual: los VLMs **alucinan con alta confianza en valores numéricos de tablas técnicas** (subsampling visual + bias del prior lingüístico + drift en tablas largas), y dos pasadas con el mismo modelo (o familia) comparten esos sesgos.

En un EETT con 80 filas de specs, accuracy típica de VLM ronda 95% — implica ~4 errores por tabla. Para procurement, donde un "3.5W" leído como "8.5W" mata la búsqueda downstream del ítem, esto es inaceptable.

El estado del arte 2024–2025 mejoró: aparecieron servicios dedicados con accuracy de tabla del 96–98%, salida nativa en Markdown, y precios competitivos. Conviene migrar a **arquitectura híbrida en 3 capas** ejecutadas por subagentes especializados:

**Capa A — Extracción primaria (determinística)**
Un servicio dedicado convierte cada PDF a Markdown con tablas extraídas correctamente y placeholders para imágenes. Opciones:
- **Mistral OCR 3** (cloud, ~$1–2 por 200 páginas, 96.6% en tablas, multilingüe robusto, MD nativo).
- **Reducto** (premium accuracy, líder en RD-TableBench, ~$0.015/página).
- **Docling** (IBM, open-source on-prem, 97.9% en tablas complejas) — opción para datos sensibles o evitar vendor lock.
- **Marker / Unstructured / LlamaParse** como alternativas open-source.
- Hyperscalers (Google Document AI, Azure Document Intelligence, AWS Textract) siguen líderes en tablas estructuradas pero más caros y sin MD nativo.

**Capa B — Enriquecimiento visual selectivo (LLM-vision)**
Sobre el MD de la capa A, un subagente con Gemini 2.5 Pro (o equivalente) recorre **solo las páginas que contienen diagramas/figuras** (detectadas en capa A) y reemplaza los placeholders por descripciones técnicas con dimensiones, tolerancias y valores visibles. Aquí los VLMs son insustituibles. Reduce el consumo de tokens del LLM 80–90% respecto al pipeline actual.

**Capa C — QA cruzado sobre tablas críticas**
Un segundo subagente con un modelo de **familia distinta** a la capa A re-extrae solo las tablas con valores numéricos críticos directamente desde la imagen de página, y se comparan **celda a celda** contra la capa A. Discrepancias se marcan `[VERIFICAR: A=3.5W, B=8.5W]` para revisión humana en lugar de elegir silenciosamente. Este patrón "cross-engine table verification" es el único que detecta alucinaciones de alta confianza, porque dos arquitecturas distintas raramente alucinan el mismo error.

**Costo estimado por proyecto de 200 páginas**: ~$0.90 con híbrido vs ~$2–4 con doble-pase LLM actual, con accuracy significativamente mayor en tablas y mecanismo real de detección de errores.

**Cuándo NO migrar**:
- Volumen muy bajo (1–2 proyectos/mes): el costo de orquestar 3 capas no justifica el esfuerzo.
- EETT mayoritariamente escaneados sin diagramas (caso raro): un VLM solo puede alcanzar.
- Si OpenClaw/Hermes no permiten tools HTTP a APIs externas: queda Docling on-prem o seguir con VLM mejorado (segundo modelo de familia distinta para el diff).

**Esfuerzo de implementación**: medio-alto. Requiere:
- Reemplazar `prompt_ocr_vision.md` por tres prompts: `prompt_ocr_extraccion_primaria.md`, `prompt_ocr_enriquecimiento_visual.md`, `prompt_ocr_qa_tablas.md`.
- Ajustar `catalog_tools.md` para incluir Mistral OCR / Docling / Reducto como tools nuevas.
- Modificar paso 1.1–1.3 del `01_workflow.md` para reflejar las 3 capas.
- Actualizar `params.yaml` con timeouts y selección de servicio por capa.

**Mitigación intermedia (sin migrar)**: si no se migra ya, el mínimo cambio que reduce el riesgo es asegurar que cuando se ejecute en modo COMPLEJO, los dos subagentes usen modelos de **familias distintas** (ej. Gemini + Claude o Gemini + GPT, no dos Gemini), para que el diff capture al menos errores no-sistémicos. Esto debería estar enforced en el workflow, no opcional.

---

## Priorización sugerida

Si tuvieras que implementar solo unas pocas, sugeriría este orden:

1. **OCR híbrido** (9) — el problema es sistémico y afecta a todo lo downstream; la base de datos de input no puede tener errores silenciosos en valores numéricos.
2. **`state.json` para reanudación** (1.1) — el dolor de perder progreso es real.
3. **Cross-check de compatibilidad parent-child** (2.1) — agrega valor visible, alinea con la realidad de armar bundles.
4. **Score numérico de candidatos** (2.2) — barato y muy útil al revisar el consolidado.
5. **Resumen ejecutivo automático** (8.3) — multiplica el valor del entregable para stakeholders.
6. **Test-suite con proyecto sintético** (5.4) — la inversión se paga sola en cuanto cambien los prompts.
