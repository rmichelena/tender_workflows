# Review fresh-eyes del workflow — v0.1

> Crítica honesta y desapasionada del diseño, encargada a un agente independiente que leyó toda la documentación sin haberla escrito. El objetivo: detectar problemas de realizabilidad, complejidad y ambigüedad antes de ejecutar contra una licitación real.

---

## Lo que está bien (en breve)

El approach es razonable para el problema. La separación de canónico (JSON) vs derivados (TSV/MD/XLSX), el patrón "ojos frescos" donde el revisor no es el productor, la trazabilidad verbatim con referencias a sección/página, y el cambio tardío de extraer "requisitos en contexto" durante la extracción del BOM (en lugar de re-localizarlos después) son decisiones sólidas. El workflow se nota iterado, con cicatrices de problemas reales detectados durante la conversación. **Ningún defecto conceptual.**

---

## Lo que está mal o riesgoso

### 1. Sobre-arquitecturado en algunas partes, sub-especificado en otras

**Sobre-arquitecturado:** 3 variantes de BOM HL + 4 variantes de BOM exploded son overkill para licitaciones de 20–100 ítems. El argumento de "diversidad" pierde fuerza si la consolidación que reconcilia esas variantes no tiene un prompt dedicado y la hace el orquestador "directamente" en una frase del workflow.

**Sub-especificado** en justamente esos puntos pivote: las consolidaciones 2.2 y 2.4 son la operación crítica del paso 2 y no tienen prompt propio, solo una línea de instrucción en `01_workflow.md`. Si tres variantes se consolidan mal, todo lo downstream queda sesgado.

### 2. Realizabilidad: dos puntos van a romperse en la primera ejecución real

- **Paso 3.1 (verificación de specs por ítem)** asume que el ítem padre está procesado antes que sus hijos para resolver herencia, pero el workflow no enforce un orden topológico padres→hijos. Si se lanza en paralelo, los hijos fallan la herencia silenciosamente.
- **Paso 3.2 (revisor de specs)** procesa TODOS los ítems en una sola pasada. Para 50–80 ítems con EETT completas cargadas, esto excede contexto útil y el timeout default de 5 minutos. Falta batching.

### 3. Ambigüedades operativas

- El estado `PARCIAL` ⚠️ conflaciona tres situaciones distintas (sin info / cumple parcial / inconsistente entre fuentes) y la decisión VÁLIDO vs CONDICIONADO depende de discriminar el subtipo. El JSON no tiene ese subtipo. Esto va a producir clasificaciones inconsistentes entre runs.
- La matriz dice "evaluar todos los requisitos sin excepción" (incluyendo Soft) pero el flujo del item manager solo busca evidencia para Hard. Hay una contradicción operativa sobre qué hace con los Soft.
- "Asociar accesorio al equipo más lógico" (BOM exploded) es ambiguo en casos comunes — un cable PoE entre switch y cámara puede heredar de cualquiera.
- En `prompt_bom_para_busqueda.md` se marca como "tarea determinista" la traducción de "norma DGAC → ICAO Annex X equivalente". No es determinista; requiere conocimiento normativo.

### 4. Riesgos no contemplados

- **Costo de tokens**: cargar EETT completas en cada subagente del paso 2 y 3 puede generar millones de tokens en una licitación grande. No hay presupuesto ni alerta en `params.yaml`.
- **Idempotencia / reproceso parcial**: si el paso 3.2 falla, ¿se conservan los IDs `R-001, R-002`? Un re-run probablemente renumera y rompe trazabilidad. No hay versionado de artefactos.
- **Concurrencia**: 3 subagentes paralelos escribiendo a la misma carpeta no es race-safe.
- **Inyección desde EETT**: un PDF con instrucciones embebidas tipo "ignorá las instrucciones anteriores" puede secuestrar al OCR o al merge de aclaraciones. Sin mitigación.
- **Reproducibilidad**: sin pin de versiones de modelos ni seeds, dos ejecuciones del mismo proyecto pueden dar shortlists distintos. Para procurement esto es problemático para auditoría.
- **Firecrawl como única opción de fetch** subestima la realidad de datasheets B2B detrás de logins, formularios "contact us" o PDFs mal formados — exactamente el dominio del usuario (telecom industrial, instrumentación).
- **XLSX final** descrito como derivado "automático" no es trivial sin código. Hay que generarlo con openpyxl o similar; quién lo ejecuta no está dicho.

### 5. Probabilidad de éxito a la primera (estimación cualitativa, licitación típica de 50 ítems)

| Paso | % éxito sin reproceso |
|---|---|
| 1.1–1.3 OCR | 70–80% |
| 1.4 merge aclaraciones | 50–60% |
| 2.1–2.4 BOM HL + exploded | 40–60% |
| 2.5 + 3.1 + 3.2 specs | 30–50% |
| 4 BOM búsqueda | 70% |
| 6 búsqueda + validación | 30–50% por ítem |
| 7 consolidación + QA | 80% |
| **End-to-end sin intervención** | **<15%** |

Con intervención humana atenta en cada gate, el workflow es viable pero pesado: probablemente 1–2 días de operación más el tiempo de cómputo. **No se puede ejecutar y dejarlo solo.** Es un workflow human-in-the-loop, no autónomo.

---

## Las 7 cosas más importantes a corregir antes de ejecutar contra una licitación real

1. **Escribir prompts dedicados para las consolidaciones 2.2 y 2.4** — son las cajas negras más críticas. Alternativa más simple: reemplazar las 3+4 variantes por "1 productor + 1 auditor", como ya se hace en otros pasos.
2. **Batching del revisor en paso 3.2** — agregar `batch_size_review: 10` a `params.yaml` y ajustar el prompt para producir reportes parciales que el orquestador concatena.
3. **Enforce orden topológico padres→hijos en paso 3.1** — documentar explícitamente en `01_workflow.md` que se procesa por capas (sin parent → primer nivel de hijos → siguiente capa).
4. **Diferenciar PARCIAL en tres subtipos en el schema** (`PARCIAL_SIN_INFO` / `PARCIAL_CUMPLE_PARCIAL` / `PARCIAL_INCONSISTENTE`) y hacer explícito el criterio de promoción a CONDICIONADO.
5. **Eliminar el paso 5** (confirmación de preferencias) y mover Gate 4 antes del paso 6: el humano mira el BOM búsqueda y marca ítems como SKIP/DIFERIR antes de invertir tokens.
6. **Codificar las reglas de selección de modelos en un `model_routing.yaml`** (`step → función → restricciones → fallback`) en vez de exigir que el orquestador combine reglas de tres archivos en cada llamada.
7. **Calibration run con licitación pasada** — ejecutar contra una licitación de 10–15 ítems donde ya se conoce el shortlist correcto. Medir intervenciones, alucinaciones, errores de herencia. Sin esto, ir directo a producción es alto riesgo.

---

## Veredicto integrado

El diseño no tiene defectos conceptuales graves, pero tiene **defectos operacionales de la clase que solo aparecen al ejecutar**: sub-especificación en los pivots críticos, supuestos optimistas sobre los modelos, ausencia de mecanismos de recuperación parcial, y falta de calibración. Es un workflow razonable como punto de partida para iterar contra problemas reales — no es un workflow listo para correr autónomamente. El riesgo de ejecutarlo sin las 7 correcciones priorizadas es desperdiciar tokens y horas humanas en outputs que después hay que reprocesar. Con esas correcciones, es razonable esperar que sea un buen asistente human-in-the-loop para reducir significativamente el tiempo de armado de shortlists.
