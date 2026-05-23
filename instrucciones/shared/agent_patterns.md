# Patrones de delegación y subagentes

> Guía operativa para decidir **cómo** delegar trabajo a LLMs/subagentes dentro del workflow `tender_procurement`.
>
> Este archivo **no define la secuencia del workflow**. La secuencia, artefactos y pasos viven en `01_workflow.md`. Si este archivo contradice `01_workflow.md` sobre orden operacional, artefactos o preparación documental, seguir `01_workflow.md` y corregir este archivo.

---

## 1. Principios

### 1.1 Delegar por intención, no por número de paso

Antes de lanzar un subagente, identificar qué tipo de trabajo se necesita:

- comprensión global;
- extracción exhaustiva;
- consolidación/deduplicación;
- verificación documental;
- auditoría;
- búsqueda externa;
- transformación determinística.

El patrón de delegación depende de ese tipo de trabajo, no del número del paso.

### 1.2 Usar el nivel mínimo de autonomía suficiente

Distinguir:

- **LLM call / subagente one-shot**: una tarea clara, un output esperado, pocas decisiones de control.
- **Workflow**: el orquestador controla una secuencia de llamadas y gates.
- **Agent autónomo**: el modelo decide herramientas, subpasos y cuándo parar.

Usar agent autónomo solo cuando la tarea sea realmente exploratoria. La mayoría del procurement funciona mejor como workflow controlado por orquestador.

### 1.3 El orquestador conserva ownership

El orquestador:

- decide alcance;
- elige patrón;
- verifica outputs;
- consolida;
- registra decisiones;
- escala dudas al humano.

El subagente produce un entregable acotado. No decide la arquitectura global.

### 1.4 No duplicar el workflow

`agent_patterns.md` no debe decir “primero limpiar PDF, luego X, luego Y”. Eso pertenece a `01_workflow.md`.

Aquí solo se documentan patrones reutilizables de delegación.

---

## 2. Verificación de modelos antes de delegar

Antes de lanzar subagentes, el orquestador debe verificar **cómo están disponibles los modelos en el entorno actual**.

### 2.1 Consultar modelos recomendados

Revisar:

- `instrucciones/model_routing.yaml`
- `ROADMAP_OBSERVATIONS.md` para evidencia reciente
- resultados de experimentos locales de la licitación actual

No elegir modelo por reputación general. Elegir por evidencia para el tipo de tarea.

### 2.2 Verificar strings reales de provider/model

Los nombres pueden variar por entorno:

- `google/gemini-2.5-flash`
- `openrouter/deepseek/deepseek-v4-flash`
- `fireworks/accounts/fireworks/models/deepseek-v4-pro`
- aliases locales
- allowlists en `openclaw.json`

Antes de lanzar:

- revisar `/status` / `session_status` cuando aplique;
- revisar configuración disponible si se tiene acceso;
- confirmar provider/model string exacto;
- si el modelo no existe en allowlist o provider, escoger alternativa documentada.

### 2.3 Auditar fallback y atribución real

No basta con saber el modelo primario de la sesión. Si hay fallbacks, el turno efectivo puede producirse con otro modelo.

Reglas:

- Para comparativos de modelo, deshabilitar fallback si la plataforma lo permite, o auditar historial por turno.
- Reportar si hubo fallback efectivo.
- No atribuir un artefacto a Gemini/DeepSeek/GPT solo porque era el modelo primario de sesión.
- Si un subagente escribe archivo, verificar qué provider/model ejecutó el turno que hizo `write` o generó el contenido.

Lección AdP: una sesión primaria Gemini puede terminar escribiendo vía fallback GPT; `status` muestra modelo de sesión, no necesariamente modelo efectivo por turno.

---

## 3. Tipos de tarea y patrón recomendado

### 3.1 Lectura libre / comprensión global

**Uso típico**: eje 0, análisis comercial/contractual general, lectura ejecutiva de un paquete de licitación.

**Patrón recomendado**:

- 1 o 2 lectores libres con modelos contrastantes.
- Prompt corto **inline**.
- Input normal: carpeta del expediente/documentos fuente, no un único archivo.
- Output: Markdown claro o JSON laxo, no schema canónico.
- Orquestador consolida, detecta discrepancias y verifica contra fuentes.

**Cuándo usar**:

- información global dispersa pero no excesivamente granular;
- el objetivo es recall/comprensión;
- el schema temprano podría distraer o reducir calidad.

**Gate de calidad**:

- cubre checklist esperado;
- marca dudas;
- identifica documentos usados;
- no inventa;
- discrepancias verificables.

### 3.2 Extracción canónica estructurada

**Uso típico**: entregables documentales, bienes/equipos, obligaciones específicas, campos que alimentan scripts downstream.

**Patrón recomendado**:

- subagente especializado por eje/documento;
- prompt claro;
- schema específico del eje;
- JSON canónico;
- validación determinística;
- Markdown derivado por script.

**Cuándo usar**:

- el output será consumido por automatización;
- se requieren enums, campos obligatorios, evidencia corta;
- hay muchas menciones pequeñas que deben ser trazables.

**Gate de calidad**:

- JSON parse;
- schema OK;
- evidencia presente;
- rangos válidos;
- cobertura declarada.

### 3.3 Consolidación / deduplicación semántica

**Uso típico**: fusionar outputs de modelos, documentos o ejes.

**Patrón recomendado**:

- LLM semantic merge, no heurística textual como fuente de verdad.
- Empezar con un consolidado base.
- Procesar nuevos inputs item por item.
- Si equivalente: fusionar procedencia y elegir mejor wording.
- Si nuevo: agregar.
- Preservar `source_documents`, `models_found`, `source_entry_ids`, evidencias y notas.

**Evitar**:

- Jaccard/string similarity como criterio final;
- consenso ciego;
- perder menciones minoritarias sin verificar.

### 3.4 Verificación documental

**Uso típico**: montos, fechas, porcentajes, garantías, plazos, discrepancias entre modelos.

**Patrón recomendado**:

- El orquestador verifica contra MD/PDF fuente.
- Usar búsqueda dirigida y lectura de contexto.
- Registrar veredicto y líneas/secciones cuando sea útil.

**Regla**:

- Los modelos detectan discrepancias; el orquestador las resuelve contra la fuente.

### 3.5 Auditoría / revisor de ojos frescos

**Uso típico**: revisar un output ya producido sin contaminarse con el razonamiento del productor.

**Patrón recomendado**:

- modelo distinto al productor;
- contexto mínimo suficiente: fuente + output + criterios de revisión;
- handoff budget 1;
- si hay fallas mayores, escalar al humano o al orquestador, no loop infinito.

### 3.6 Búsqueda externa / candidatos

**Uso típico**: encontrar productos/proveedores/candidatos fuera del expediente.

**Patrón recomendado**:

- fan-out paralelo controlado;
- tool budget explícito;
- cada worker con alcance estrecho;
- orquestador consolida.

### 3.7 Transformación determinística

**Uso típico**: render Markdown desde JSON, validar schema, convertir formatos, calcular conteos, mover/archivar archivos.

**Patrón recomendado**:

- scripts/Python/herramientas determinísticas;
- no usar LLM para parseo trivial o conversión mecánica.

---

## 4. Diseño del handoff

### 4.1 Prompt inline vs ruta de prompt

Usar **prompt inline** cuando:

- el prompt es corto;
- la instrucción semántica es central;
- se quiere máxima adherencia;
- el modelo podría distraerse con meta-trabajo.

Usar **ruta de prompt** cuando:

- el prompt es largo o generado;
- hay plantillas versionadas extensas;
- el subagente ya está probado para leer herramientas correctamente.

Evitar el meta-prompt “lee el prompt en esta ruta y luego haz la tarea” si el prompt cabe inline.

### 4.2 Carpeta fuente vs documento único

Input normal para licitaciones: **carpeta del expediente/documentos fuente**.

Porque:

- AAP/AdP/LAP suelen tener Bases + EETT/TDR/anexos;
- Estado peruano puede tener documento consolidado o principal + anexos;
- OACI/ICAO/BID suelen dispersar información entre varios documentos.

Pasar un único documento solo si:

- el experimento está explícitamente limitado;
- el workflow lo define así;
- el documento consolidado realmente contiene todo.

### 4.3 Inventario de documentos

Cuando sea posible, incluir un inventario breve:

- nombre de archivo;
- tipo probable: bases, expediente técnico, anexo, formulario, aclaración;
- ruta.

El inventario ayuda al subagente a navegar sin convertir el handoff en un volcado de contenido.

### 4.4 Paths vs contenido inline

Regla actualizada:

- Documentos grandes: pasar paths/carpeta.
- Prompts cortos: inline.
- Schemas largos: path.
- Extractos pequeños críticos: pueden ir inline si reducen ambigüedad.
- Tablas/listas cortas de parámetros: inline.

La regla no es “nunca contenido”; es “no volcar documentos grandes como contexto cuando el subagente puede leerlos”.

### 4.5 Definition of Done

Todo handoff debe decir explícitamente:

- qué leer;
- qué producir;
- dónde escribir;
- formato esperado;
- qué hacer si falta información;
- cómo reportar dudas;
- si debe validar o no.

---

## 5. Context engineering

### 5.1 Write / select / compress / isolate

Usar:

- **Write**: registrar decisiones en archivos (`ROADMAP_OBSERVATIONS.md`, logs, scratchpads).
- **Select**: recuperar secciones relevantes cuando no hace falta todo el paquete.
- **Compress**: resumir contexto cuando la conversación se vuelve larga.
- **Isolate**: subagentes con contexto propio para lecturas independientes.

### 5.2 Offsets no son chunking semántico

Cuando `read` trunca archivos largos, leer por offsets puede ser solo transporte técnico.

No confundir:

- “leer por offsets para superar límite de herramienta”
- con “extraer por chunks semánticos con cobertura y schema”.

El primer caso puede seguir siendo lectura libre/holística.

### 5.3 Compartir decisiones, no solo outputs

Si varios subagentes deben componer un resultado, el orquestador debe compartir decisiones relevantes:

- naming;
- alcance;
- clasificación;
- supuestos;
- exclusiones;
- criterios de dedupe.

Cuando no se comparten, cada modelo inventa su propia taxonomía y la consolidación pierde información.

---

## 6. Outputs y gates

### 6.1 Markdown libre / semiestructurado

Apto para:

- comprensión global;
- lectura ejecutiva;
- análisis de discrepancias;
- primer pase de recall.

Gate:

- checklist completo;
- claridad;
- dudas explícitas;
- fuentes identificadas;
- revisión del orquestador.

### 6.2 JSON canónico

Apto para:

- downstream automatizado;
- dedupe sistemático;
- render determinístico;
- matrices y scripts.

Gate:

- JSON parse;
- schema;
- validaciones adicionales;
- retry una vez con error si procede;
- segundo fallo = falla loud.

### 6.3 Markdown derivado

Si existe JSON canónico, el Markdown humano debe derivarse por script, no por el subagente.

Así se puede cambiar formato sin relanzar LLM.

### 6.4 Discrepancias

Las discrepancias son output valioso.

Registrar:

- qué modelos/documentos discrepan;
- cuál es el dato conflictivo;
- verificación contra fuente;
- veredicto;
- si queda incertidumbre.

---

## 7. Fallbacks, fallas y atribución

### 7.1 Falla loud antes que éxito ambiguo

Si el subagente no cumple Definition of Done:

- no inventar éxito;
- no asumir que un archivo parcial sirve;
- reportar fallo y evidencia.

### 7.2 Fallback audit

Para pruebas de modelo:

- verificar modelo primario;
- verificar fallbacks configurados;
- revisar historial por turno si está disponible;
- confirmar qué modelo produjo el artefacto.

### 7.3 Gemini/subagente

Lección observada:

- Gemini directo/chat puede funcionar.
- Gemini como subagente con herramientas/contexto largo puede desviarse, fallar o caer a fallback.
- No atribuir output a Gemini sin auditar provider/model efectivo del turno productor.

---

## 8. Anti-patterns

Evitar:

1. **Mini-workflow duplicado**: `agent_patterns.md` no debe repetir `01_workflow.md`.
2. **Meta-prompt innecesario**: pasar prompts cortos solo como ruta.
3. **Schema-first para comprensión global**: puede reducir recall y empeorar razonamiento.
4. **Free-text cuando hace falta canon**: si downstream requiere estructura, usar JSON/schema.
5. **Dedupe semántico con heurística textual**: Jaccard/string similarity solo como ayuda, no decisión final.
6. **Consenso sin verificación**: dos modelos pueden coincidir y estar mal; uno puede estar solo y tener razón.
7. **Fallback invisible**: reportar un modelo sin revisar si hubo fallback efectivo.
8. **Un documento por defecto**: asumir que “Bases” contiene todo el expediente.
9. **Anti-instrucciones distractoras**: decir “no uses chunk plan/no schema/no JSON” a un subagente que no tiene ese contexto.
10. **LLM para trabajo mecánico**: render, validación, conteos, conversiones simples deben ser determinísticos.

---

## 9. Lecciones históricas útiles, no reglas absolutas

La corrida ICAO-00068 mostró problemas reales:

- JSON truncado cuando no hay validación/gates;
- variantes paralelas inconsistentes;
- falta de scratchpad compartido;
- context dumps costosos;
- retries sin budget.

Pero esas lecciones no implican que todo deba ser JSON-first ni chunked.

Regla viva:

> Elegir patrón según la tarea actual y la evidencia actual. Mantener el workflow como autoridad de secuencia; mantener este archivo como guía de delegación.
