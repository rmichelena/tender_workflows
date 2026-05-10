# Prompt — Subagente Verificación + Refinamiento + Herencia de Especificaciones (Paso 3.1)

> **Cambio de rol respecto al diseño inicial**: este subagente ya **no extrae specs desde cero** (eso lo hizo el paso 2 al producir el BOM con requisitos en contexto). Ahora **verifica, refina y resuelve herencia**:
> 1. **Verifica completitud** — ¿faltan requisitos del ítem en las EETT?
> 2. **Normaliza** — formato consistente, deduplicación, agrupación lógica.
> 3. **Clasifica** hard / soft.
> 4. **Resuelve herencia** — incorpora requisitos del ítem padre que aplican técnicamente.

## Inputs

- `{ITEM_BASE_JSON}`: ruta al ítem base (`ITEM-{id}.json` del paso 2.5, con `requisitos_en_contexto` ya extraídos).
- `{EETT_ACLARADAS}`: archivos markdown de EETT aclaradas.
- `{ANEXOS_ACLARADOS}`: archivos markdown de anexos aclarados (puede estar vacío).
- `{ITEM_PADRE_JSON}`: si el ítem tiene `parent_id`, ruta al `ITEM-{parent_id}_specs.json` ya procesado (puede ser opcional si se procesa en orden de padres → hijos).
- `{OUTPUT_PATH_JSON}`: ruta donde escribir el ítem con specs verificadas (canónico).

## Procedimiento

### 1. Cargar el ítem base

Leé `{ITEM_BASE_JSON}`. Extraé:
- `item_id`, `nombre`, `tipo`, `grupo`, `parent_id`.
- Lista de `requisitos_en_contexto` ya extraídos en paso 2 (estos son tu punto de partida — no los extraigas otra vez).

### 2. Verificación de completitud (¿falta algo?)

Recorré la sección de las EETT correspondiente al ítem (referencia ya está en el JSON base). Verificá si hay requisitos explícitos que **no estén** en `requisitos_en_contexto`:
- Requisitos en texto narrativo que pudieron pasarse por alto en paso 2.
- Requisitos en notas al pie que pudieron ignorarse.
- Requisitos generales del subsistema/grupo que aplican al ítem (ej. "todos los equipos deberán cumplir...", "la totalidad del sistema debe...").

Si encontrás requisitos faltantes, agregalos a la lista, marcando `origen_extraccion: "ANADIDO_EN_VERIFICACION"`.

### 3. Normalización

Sobre la lista combinada (originales del paso 2 + añadidos en verificación):
- Deduplicar requisitos idénticos o equivalentes (consolidar referencias).
- Mantener el texto **verbatim** (no parafrasear).
- Asignar IDs estables `R-001`, `R-002`, ...

### 4. Clasificación Hard / Soft

Para cada requisito:
- **Hard**: frases con "deberá", "obligatorio", "mínimo", "máximo", "se requiere", "cumplirá", "no menor a", "no mayor a", y cualquier valor cuantitativo explícito (salvo que diga "preferible" o "deseable").
- **Soft**: "deseable", "preferentemente", "opcional", "se recomienda", "se valorará".

### 5. Herencia de requisitos

Si `parent_id` no está vacío:
- Cargá `{ITEM_PADRE_JSON}` (specs del padre ya procesado).
- Evaluá qué requisitos del padre aplican **técnicamente** al ítem hijo. Ejemplos:
  - Antena hereda banda de frecuencia de la radio a la que se conecta.
  - Cable coaxial hereda impedancia y tipo de conector.
  - Protector de descarga hereda banda de frecuencia del sistema.
  - Fuente de alimentación hereda voltaje requerido por el equipo.
- **NO heredar** requisitos que no apliquen (ej. dimensiones físicas del equipo principal a un cable).

Para cada requisito heredado, agregarlo con:
- `origen: "HEREDADO"`
- `origen_padre: "IT-XXXX"`
- `texto_verbatim`: copiado tal cual del padre
- `referencia`: la fuente original del padre
- `notas_herencia`: explicación breve de por qué aplica (ej. "antena debe operar en la misma banda que el equipo principal")

### 6. TBD / Ambigüedades

Si encontrás puntos ambiguos durante la verificación, agregalos a `tbd[]` (sin convertirlos en requisito).

## Output canónico — `ITEM-{id}_specs.json`

Conforme a `schemas/item_specs.schema.json`.

```json
{
  "item_id": "IT-0001",
  "nombre": "Radio base VHF digital",
  "tipo": "BIEN",
  "grupo": "Comunicaciones",
  "parent_id": "",
  "cantidad": "2",
  "unidad": "und",
  "referencia_eett": "EETT_01_aclarada.md, Sección 4.2, PAGE 12",
  "estado_specs": "VERIFICADO",
  "fuentes_consultadas": ["EETT_01_aclarada.md"],
  "requerimientos": [
    {
      "req_id": "R-001",
      "texto_verbatim": "Frecuencia de operación: 118-136.975 MHz, separación entre canales 25 kHz / 8.33 kHz",
      "hard_soft": "Hard",
      "origen": "DIRECTO",
      "origen_extraccion": "PASO_2",
      "origen_padre": null,
      "referencias": ["EETT_01_aclarada.md, Sección 4.2, PAGE 12"],
      "notas": ""
    },
    {
      "req_id": "R-002",
      "texto_verbatim": "Potencia de salida mínima: 5W.",
      "hard_soft": "Hard",
      "origen": "DIRECTO",
      "origen_extraccion": "PASO_2",
      "origen_padre": null,
      "referencias": ["EETT_01_aclarada.md, Sección 4.2, PAGE 12"],
      "notas": ""
    },
    {
      "req_id": "R-003",
      "texto_verbatim": "Todos los equipos de comunicación deberán contar con homologación MTC vigente.",
      "hard_soft": "Hard",
      "origen": "DIRECTO",
      "origen_extraccion": "ANADIDO_EN_VERIFICACION",
      "origen_padre": null,
      "referencias": ["EETT_01_aclarada.md, Sección 2.1 (requisito general), PAGE 5"],
      "notas": "Aplica por ser requisito general del subsistema"
    }
  ],
  "tbd": [
    {
      "descripcion": "No se especifica rango de temperatura de operación",
      "referencia": "EETT_01_aclarada.md, Sección 4.2",
      "falta": "Definir rango de temperatura ambiente"
    }
  ]
}
```

**Para un ítem con herencia (ejemplo: antena hija de una radio)**:

```json
{
  "item_id": "IT-0005",
  "nombre": "Antena VHF omnidireccional",
  "tipo": "BIEN",
  "grupo": "Comunicaciones",
  "parent_id": "IT-0001",
  "estado_specs": "VERIFICADO",
  "fuentes_consultadas": ["EETT_01_aclarada.md"],
  "requerimientos": [
    {
      "req_id": "R-001",
      "texto_verbatim": "Antena omnidireccional, ganancia mínima 5 dBi, polarización vertical.",
      "hard_soft": "Hard",
      "origen": "DIRECTO",
      "referencias": ["EETT_01_aclarada.md, Sección 4.3, PAGE 14"]
    },
    {
      "req_id": "R-002",
      "texto_verbatim": "Frecuencia de operación: 118-136.975 MHz",
      "hard_soft": "Hard",
      "origen": "HEREDADO",
      "origen_padre": "IT-0001",
      "referencias": ["EETT_01_aclarada.md, Sección 4.2, PAGE 12"],
      "notas_herencia": "La antena debe operar en la misma banda que la radio base (IT-0001)"
    }
  ],
  "tbd": []
}
```

## Output derivado — `ITEM-{id}_specs.md`

Vista markdown con frontmatter YAML idéntico al JSON, y la lista de requerimientos como tabla legible.

## Reglas no negociables

- **No inventar requerimientos**: solo lo explícito en EETT/aclaradas o lo razonablemente heredado.
- **Verbatim**: nunca parafrasear el texto del requisito.
- **Trazabilidad obligatoria**: cada requisito debe tener al menos una referencia.
- **Marcar el origen**: `DIRECTO` vs `HEREDADO`, y para directos `PASO_2` vs `ANADIDO_EN_VERIFICACION`.

## Entrega

Escribí el resultado en `{OUTPUT_PATH_JSON}` (y el `.md` derivado correspondiente).
Devolvé:
- `OK: {OUTPUT_PATH_JSON}`
- `ITEM-{id}: {N} reqs (Hard: {H}, Soft: {S}, Heredados: {K}, TBD: {T})`
- Si hubo `ANADIDO_EN_VERIFICACION`: `Añadidos en verificación: {A}` (señal de que el paso 2 había omitido requisitos).
