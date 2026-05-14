# Auto-Evaluación del Workflow ICAO-00068

**Fecha**: 2026-05-13
**Sesión**: ~7 horas de procesamiento continuo
**Resultado**: Workflow completado end-to-end (7 pasos), pero con significativas dificultades operativas

---

## Resumen Ejecutivo

El workflow completo de licitación ICAO-00068 se ejecutó de punta a punta, desde la normalización documental hasta la consolidación final con QA. Sin embargo, el camino fue considerablemente más accidentado de lo que el diseño del workflow anticipaba. Este documento cataloga los incidentes, identifica causas raíz, y propone ajustes concretos al workflow, los prompts, y la infraestructura para futuras ejecuciones.

**Métricas clave**:
- Pasos completados: 8/8 (1.1-1.4, 2.1-2.5, 3.1-3.2, 4, 5, 6, 7.1-7.2)
- Items BOM final: 155 (94 VSAT, 18 Radio, 17 Infra, 15 Servicios, 11 Energía)
- Requerimientos extraídos: 1000 (993 Hard, 7 Soft)
- Items con candidato comercial: 15/140 (10.7%)
- QA final: OK (0 hallazgos críticos)

---

## Catálogo de Incidentes

### INC-001: Subagentes delegate_task fallan con documentos grandes ⚠️ AUTO-INFLIGIDO
**Paso**: 1.4, 2.1, 2.3
**Síntoma**: Timeout consistente (600s) cuando los documentos de input superan ~100K chars
**Causa raíz**: **ERROR DEL ORQUESTADOR, no limitación de delegate_task**. El orquestador estaba cargando el contenido completo de los archivos (258K chars) en el campo `context` de delegate_task, en vez de pasarle **file paths** y dejar que el subagente leyera los archivos por su cuenta. Los subagentes tienen acceso a `read_file`, `terminal`, y otras herramientas — pueden leer archivos del filesystem directamente. El patrón correcto es:
```
# ❌ Lo que se hizo
delegate_task(context=contenido_258K_chars, goal="extraé el BOM...")
# ✅ Lo correcto
delegate_task(context="Lee /path/to/specs.md. Schema en /path/to/schema.json. Output en /path/to/output.json", goal="Extraer BOM", toolsets=["file","terminal"])
```
**Workaround aplicado**: Scripts Python + API directa Fireworks (funcional pero innecesario)
**Impacto**: 3 reintentos desperdiciados + todo el overhead de scripts ad-hoc. ~45 min perdidos + complejidad innecesaria.
**Corrección**: En futuras ejecuciones, SIEMPRE pasar file paths en context, nunca file contents. El subagente lee por su cuenta, puede procesar por secciones, y escribe output a disco.

### INC-002: LLMs no producen JSON válido a max_tokens=16384
**Paso**: 2.1, 2.3
**Síntoma**: Outputs truncados en medio de un JSON array, especialmente con kimi-k2p6, deepseek-v4-pro, minimax-m2p7
**Causa raíz**: Con prompts de ~180K chars input, los modelos generan JSON que excede 16384 tokens antes de cerrar el array. glm-5p1 fue el único que produjo JSON válido directamente (compacta mejor).
**Workaround aplicado**: (1) Subir max_tokens a 32768, (2) Implementar "bracket counting rescue" que extrae objetos JSON individuales del output truncado.
**Impacto**: 2 rondas de retry para BOM HL, 1 ronda para BOM Exploded. ~30 min perdidos.

### INC-003: Instrucciones subóptimas al subagente (Paso 1.4)
**Paso**: 1.4
**Síntoma**: Subagente ejecutor produjo ITB V2 aclarado OK pero Tech Specs sin cambios
**Causa raíz**: La instrucción era "re-escribí el documento completo con las aclaraciones incorporadas" en lugar de "aplicá patches quirúrgicos con marca de trazabilidad". El subagente intentó regenerar 258K chars en vez de hacer ediciones atómicas.
**Lección**: Los subagentes operan sobre archivos. Dar instrucciones de edición quirúrgica, no de re-escritura completa.
**Impacto**: Tech Specs aclarado quedó incompleto, requirió intervención manual.

### INC-004: Paso 2.4 (Consolidación BOM Exploded) requiere chunks
**Paso**: 2.4
**Síntoma**: 504 timeout cuando se envían 426 items (4 variantes, ~120K chars) en un solo prompt
**Causa raíz**: El prompt de consolidación con 426 items + instrucciones excede el límite práctico de Fireworks (~60K chars input útil para output de 49K tokens)
**Workaround aplicado**: Particionar por grupo (VSAT=275 items en chunks de 55, otros grupos de a 1)
**Impacto**: Chunk 4 de VSAT falló y requirió retry con deepseek-v4-pro. ~20 min extra.

### INC-005: Paso 3.1 (Specs) completitud insuficiente en primera pasada
**Paso**: 3.1
**Síntoma**: Solo 107/155 items con specs después de la primera pasada (718/1000 requerimientos)
**Causa raíz**: Batches de 10 items saturan el output del modelo. JSON truncado para los últimos items de cada batch.
**Workaround aplicado**: Re-lanzar con batches de 5 items usando glm-5p1. 48 items pendientes completados (282 reqs adicionales).
**Impacto**: Segunda ronda completa de API calls. ~25 min extra.

### INC-006: Revisión "ojos frescos" encontró 28 FALTANTE
**Paso**: 3.2
**Síntoma**: minimax-m2p7 detectó 38 correcciones en 18 items, predominantemente FALTANTE (requisitos de EETT no extraídos)
**Causa raíz**: El paso 2 (BOM con "requisitos en contexto") no capturó todos los requisitos. La premisa del diseño era que el paso 2 extraería requisitos in-context y el paso 3 solo verificaría/refinaría, pero en práctica el paso 3 tuvo que hacer extracción sustancial.
**Impacto**: 28 requisitos faltantes documentados en correcciones_pendientes.md. Solo correcciones de clasificación se aplicaron automáticamente; los FALTANTE requieren revisión manual.

### INC-007: Búsqueda en español produce 0 resultados
**Paso**: 6
**Síntoma**: Primera versión del script de búsqueda usó queries en español → 0% hit rate
**Causa raíz**: Tinyfish (y la web en general) indexa productos de telecomunicaciones predominantemente en inglés. Términos como "filtro banda C satélite" no matchean nada.
**Workaround aplicado**: Traducir queries a inglés ("C-band satellite filter"). Hit rate subió a ~11%.
**Impacto**: Primera ronda de búsqueda desperdiciada (~140 items). ~40 min perdidos.

### INC-008: Hit rate general bajo (10.7%)
**Paso**: 6
**Síntoma**: Solo 15/140 items con candidatos válidos/condicionados
**Causa raíz** (múltiple):
1. Muchos items son repuestos genéricos (cables, conectores, SFPs) sin producto comercial específico
2. Tinyfish Search es generalista, no accede a catálogos B2B especializados (GigaParts, TESSCO, Westbase, etc.)
3. Las queries autogeneradas son demasiado genéricas para items complejos
4. La evaluación glm-5p1 es conservadora — clasifica como CONDICIONADO lo que podría ser VÁLIDO con más contexto
**Impacto**: 125 items SIN_CANDIDATO requieren trabajo manual posterior.

### INC-009: Firecrawl sin créditos
**Paso**: 6
**Síntoma**: No se pudo usar Firecrawl para parsear datasheets/fichas técnicas de fabricantes
**Causa raíz**: Créditos agotados
**Workaround aplicado**: Usar Tinyfish Fetch como fallback
**Impacto**: Fetch de Tinyfish es menos capaz para parsear páginas complejas de fabricantes con JS rendering. Menor calidad de evidencia.

### INC-010: XLSX falla por openpyxl no en sys.path
**Paso**: 7.1
**Síntoma**: Script de consolidación no puede importar openpyxl
**Causa raíz**: pip instala en `--target /opt/data/home/.local/lib/python3.13/site-packages` pero scripts no lo tienen en sys.path
**Workaround aplicado**: Agregar `sys.path.insert(0, ...)` al inicio del script
**Impacto**: Menor, pero requiere parchear cada script nuevo.

---

## Análisis de Causas Sistémicas

### 1. El workflow asume subagentes delegate_task como mecanismo principal

El workflow fue diseñado para usar `delegate_task` como mecanismo de ejecución para la mayoría de pasos (1.4, 2.1, 2.3, 3.1, 3.2, 6). En práctica, delegate_task es inadecuado para:
- Documentos >50K chars (timeout)
- Tareas que requieren API calls externas (Fireworks, Tinyfish)
- Outputs estructurados grandes (JSON de 100+ items)

**El patrón que funcionó**: scripts Python que llaman APIs directamente, con el orquestador coordinando la lógica de alto nivel y los scripts haciendo el trabajo pesado.

### 2. Los prompts de BOM son ambiciosos para la capacidad real de los modelos

Los prompts `prompt_bom_highlevel.md` y `prompt_bom_exploded.md` piden:
- Extracción de items con descripción completa
- Requisitos "en contexto" verbatim con trazabilidad
- Checklist de cobertura
- Formato JSON estricto

Con 180K chars de input, ningún modelo produjo todo esto completo. Los requisitos en contexto fueron los primeros en truncarse. La trazabilidad fue inconsistentemente producida. Los checklists de cobertura casi nunca se generaron.

### 3. La premisa "paso 2 extrae requisitos, paso 3 verifica" no se cumple

El diseño asume que el paso 2 (BOM) captura requisitos in-context y el paso 3 solo verifica/refina. En realidad:
- Los modelos priorizan la extracción de items sobre los requisitos asociados
- Los requisitos en contexto se truncaban cuando el output se acercaba al límite de tokens
- El paso 3 terminó haciendo extracción significativa (28 FALTANTE detectados por el revisor)
- Esto significa que el paso 3 necesita ser rediseñado como extracción + verificación, no solo verificación

### 4. La búsqueda de equipamiento (Paso 6) está sub-diseñada

El prompt `prompt_search_worker.md` y `prompt_item_manager.md` asumen:
- 2 workers paralelos con modelos y tools distintas
- Rotación de combos en reintentos
- Validación contra fuente primaria (datasheet)
- Matrices de cumplimiento por candidato

En la implementación real:
- No hay 2 search providers distintos (solo Tinyfish)
- Firecrawl (para parsear datasheets) no tiene créditos
- La búsqueda web generalista no accede a catálogos B2B
- El hit rate de 10.7% es insuficiente para una propuesta de licitación

### 5. El ID scheme cambió en ejecución

El workflow usa IDs `IT-0001`, `HL-001`. La ejecución real produjo IDs `EXP-001`, `HL-001`. Esto causó inconsistencias menores en los scripts de consolidación.

---

## Recomendaciones de Ajuste

### A. Patrón Correcto para delegate_task (CORRECCIÓN)

**Problema**: El orquestador pasaba file contents en vez de file paths, causando timeouts artificiales
**Solución**: Usar delegate_task correctamente — pasar file paths en context, el subagente lee por su cuenta:

```python
# Patrón correcto
delegate_task(
    goal="Extraer BOM High-Level del documento de especificaciones técnicas",
    context=(
        "INPUT: lee el archivo /path/to/EETT_aclarada.md\n"
        "SCHEMA: lee /path/to/bom_item.schema.json para formato de salida\n"
        "PROMPT: sigue las instrucciones en /path/to/prompt_bom_highlevel.md\n"
        "OUTPUT: escribe el resultado JSON en /path/to/output.json\n"
        "REPORT: solo reporta status por stdout (n items, n reqs, grupos detectados)"
    ),
    toolsets=["file", "terminal"]
)
```

El subagente puede:
- Leer archivos grandes en chunks con `read_file(offset, limit)`
- Procesar sección por sección
- Escribir resultados intermedios y finales a disco
- Reportar solo el summary de vuelta

**Esto elimina la necesidad de scripts Python + API directa** para la mayoría de los pasos del workflow. Los scripts solo se necesitan para tareas puramente mecánicas (paso 2.5 item pack, paso 4 filtrado, paso 7 consolidación).

**Los scripts Python + API directa** se mantienen como opción válida para pasos donde se necesita control fino sobre el modelo (e.g., especificar exactamente qué modelo Fireworks usar, o hacer llamadas con parámetros no estándar), pero NO como mecanismo principal.

### B. Ajuste de Prompts de BOM

**Problema**: Los prompts piden demasiado (items + requisitos en contexto + checklist) y los requisitos son lo primero que se pierde al truncar

**Soluciones**:

1. **Separar extracción de items y extracción de specs en dos fases**:
   - Fase A: BOM items solamente (id, descripción, cantidad, grupo, referencia sección) — output compacto, alta completitud
   - Fase B: Por cada item, extraer specs de la sección referenciada — batches de 5 items, prompt con solo la sección relevante (no todo el doc)

   Esto alinea el diseño con lo que los modelos realmente pueden hacer: items compactos en una pasada, specs en una segunda pasada por sección.

2. **Simplificar el formato de salida del BOM**:
   - Eliminar `checklist_cobertura` (nunca se produjo bien)
   - Reducir `requisitos_en_contexto` a solo `seccion_referencia` + `count_requisitos` (los requisitos reales se extraen en fase B)
   - Usar `id` numérico simple (001, 002) en vez de prefijos (HL-, IT-, EXP-)

3. **Añadir instrucciones anti-truncamiento al prompt**:
   - "Si te acercás al límite de output, prioriza completar los items que ya empezaste sobre agregar más items. Es preferible 20 items completos que 40 truncados."
   - "NO incluyas el texto completo de los requisitos en el BOM. Solo referenciá la sección del EETT donde aparecen."

### C. Rediseño del Paso 3 (Specs)

**Problema**: El paso 3 debería ser extracción + verificación, no solo verificación

**Solución**:

1. Renombrar el paso de "Verificación + Herencia" a "Extracción Completa + Clasificación + Herencia"
2. El prompt debe asumir que los items del BOM tienen referencias a secciones pero NO necesariamente requisitos extraídos
3. Procesar en batches de 5 items, con solo las secciones relevantes del EETT (no todo el doc)
4. Incluir en el prompt: "Para cada item, leé la sección XXX del EETT referenciada en el BOM. Extraé TODOS los requisitos explícitos (cantidades, dimensiones, frecuencias, certificaciones, estándares, materiales)."

### D. Rediseño del Paso 6 (Búsqueda)

**Problema**: Hit rate 10.7%, búsqueda web generalista insuficiente

**Soluciones**:

1. **Pre-clasificar items en 3 tiers antes de buscar**:
   - **Tier 1 (buscar activamente)**: Equipos principales con specs suficientes (modems, radios, antenas, UPS, switches) — ~30 items
   - **Tier 2 (buscar con queries amplias)**: Componentes genéricos con specs parciales (cables, conectores, racks) — ~50 items
   - **Tier 3 (no buscar, marcar MANUAL)**: Repuestos, obras civiles, kits sin specs comerciales, servicios — ~60 items

   Esto evita desperdiciar ~60 búsquedas en items que nunca van a tener candidato web.

2. **Queries bilingües**: Generar queries en inglés Y español, priorizar inglés pero intentar español como fallback.

3. **Búsqueda por marca preferida primero**: Si el overlay dice "Comtech preferido para modems", buscar específicamente "Comtech satellite modem" antes que "satellite modem" genérico.

4. **Fallback a búsqueda manual asistida**: Para Tier 1 items sin candidato, generar una ficha de búsqueda estructurada que un humano pueda usar en catálogos B2B.

5. **Obtener créditos Firecrawl o alternativa para parseo de datasheets**: Sin capacidad de parsear páginas de fabricante, la validación de specs es imposible. Alternativa: usar Playwright/Browser tool para fetch + extracción de texto.

### E. Ajustes Operativos

1. **sys.path en template de scripts**: Incluir siempre `sys.path.insert(0, '/opt/data/home/.local/lib/python3.13/site-packages')` en el template.

2. **Normalizar ID scheme**: Usar siempre formato `{grupo}-{número}` (ej. `VSAT-001`, `RADIO-003`, `INFRA-007`) en vez de prefijos genéricos que cambian entre pasos.

3. **Log de ejecución automático**: Los scripts deben escribir automáticamente al log (fecha, modelo, items procesados, tokens usados, errores) en vez de depender del orquestador.

4. **Guardado intermedio automático a Drive**: Cada paso debe subir sus artifacts a Drive inmediatamente al completar, no esperar al final.

---

## Evaluación por Paso

| Paso | Dificultad | Tiempo Real vs Estimado | Hallazgo Principal |
|------|-----------|------------------------|-------------------|
| 1.1-1.3 | Baja | ~5 min / ~10 min | MarkItDown + LandingAI funcionan bien |
| 1.4 | Alta | ~30 min / ~15 min | Instrucciones de re-escritura vs patch |
| 2.1 | Alta | ~60 min / ~20 min | API directa > delegate_task, truncamiento JSON |
| 2.2 | Media | ~15 min / ~10 min | Consolidación directa OK |
| 2.3 | Alta | ~45 min / ~20 min | 4 variantes, truncamiento, rescue |
| 2.4 | Alta | ~40 min / ~10 min | Chunks necesarios, retry chunk 4 |
| 2.5 | Baja | ~3 min / ~5 min | Determinista, sin problemas |
| 3.1 | Alta | ~50 min / ~20 min | Segunda pasada necesaria, batches de 5 |
| 3.2 | Media | ~20 min / ~15 min | 28 FALTANTE detectados |
| 4 | Baja | ~5 min / ~5 min | Ejecución directa sin problemas |
| 5 | N/A | ~0 min | Auto-assumed |
| 6 | Alta | ~90 min / ~60 min | Queries español→inglés, hit rate bajo |
| 7.1 | Baja | ~5 min / ~5 min | Consolidación OK |
| 7.2 | Baja | ~2 min / ~5 min | QA OK |

**Total real**: ~5.5 horas
**Total estimado (workflow)**: ~3.5 horas
**Overhead por incidentes**: ~2 horas (36% del tiempo total)

---

## Modelo de Madurez del Workflow

Estado actual: **Nivel 2 — Repetible con workarounds**

| Nivel | Descripción | Estado |
|-------|-------------|--------|
| 1 | Ad-hoc, cada ejecución es diferente | ❌ Superado |
| 2 | Repetible con workarounds manuales | ✅ Actual |
| 3 | Definido, scripts estables, prompts afinados | Próximo objetivo |
| 4 | Gerenciado, métricas, auto-retry, logging automático | — |
| 5 | Optimizado, auto-tuning de prompts/batches | — |

Para alcanzar Nivel 3, los ajustes A-E arriba son necesarios. Estimo ~4 horas de trabajo para implementarlos.

---

## Anexos

### A. Scripts creados durante la sesión

| Script | Propósito | Reutilizable |
|--------|-----------|-------------|
| `bom_highlevel_extract.py` | v1, falló por doc completo | ❌ |
| `bom_highlevel_v2.py` | v2, colgado por loop | ❌ |
| `bom_hl_v3.py` | v3, API directa + filtrado secciones | ✅ Template |
| `bom_hl_rerun.py` | Retry kimi/deepseek | ❌ |
| `normalize_bom_hl.py` | Normalizar 3 variantes | ✅ Reutilizable |
| `step3_specs_retry.py` | Specs en batches de 5 | ✅ Template |
| `step6_busqueda_v2.py` | Búsqueda con Tinyfish + glm-5p1 | ✅ Template |
| `step7_consolidacion.py` | Consolidación final | ✅ Reutilizable |
| `step7_qa.py` | QA final | ✅ Reutilizable |

### B. Tokens API consumidos (estimación)

| Componente | Llamadas | Tokens input (est.) | Tokens output (est.) |
|------------|---------|--------------------|--------------------|
| BOM HL (3 variantes) | 6 | ~1.1M | ~100K |
| BOM Exploded (4 variantes) | 8 | ~1.5M | ~200K |
| Consolidación BOM | 6 | ~500K | ~80K |
| Specs (2 pasadas) | 35 | ~2M | ~300K |
| Revisión specs | 5 | ~200K | ~50K |
| Búsqueda (evaluación) | 140 | ~1M | ~140K |
| **Total** | ~200 | ~6.3M | ~870K |

### C. Archivos generados

| Tipo | Cantidad | Tamaño total |
|------|----------|-------------|
| JSON | ~475 | ~2.5 MB |
| MD | ~325 | ~1.8 MB |
| TSV | 5 | ~120 KB |
| XLSX | 1 | ~28 KB |
| PY | 9 | ~45 KB |
| ZIP (Drive) | 7 | ~863 KB |
