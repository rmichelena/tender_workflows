# Prompt — Generación de Item Pack (Paso 2.5) — v0.2

> **Tipo**: tarea determinística del orquestador. NO requiere LLM. Es transformación mecánica BOM Exploded JSON → un archivo JSON+MD por item. Se ejecuta con script o handler determinístico, NO se delega a un subagente con razonamiento.

A partir del BOM Exploded consolidado, generá un par de archivos (JSON canónico + MD derivado) por cada item. Estos archivos sirven como base atómica para el Paso 3 (verificación de specs + herencia) y Paso 6 (búsqueda).

## Inputs

- `{BOM_EXPLODED_JSON}`: ruta al BOM exploded consolidado en JSON.
- `{OUTPUT_DIR}`: directorio donde escribir los archivos individuales.

## Instrucciones

1. Leer el BOM exploded JSON completo.
2. Por cada `item` del array `items`, generar:
   - Un archivo canónico `{OUTPUT_DIR}/ITEM-{id}.json` (estructura indicada abajo).
   - Un archivo derivado `{OUTPUT_DIR}/ITEM-{id}.md` (vista humana).
3. **Transcribir** los datos exactamente como aparecen en el BOM (no interpretar, no enriquecer, no agregar). Los `requisitos_en_contexto` se heredan tal cual del BOM — el paso 3 los verificará/normalizará/clasificará.
4. El campo `parent_id` se completa con el `parent_id` del BOM si existe; si no tiene padre, dejar `""`.

## Output canónico — `ITEM-{id}.json`

Conforme a `schemas/item_specs.schema.json` (en estado base, sin clasificación hard/soft ni herencia resuelta).

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
  "estado_specs": "PENDIENTE_VERIFICACION",
  "requisitos_en_contexto": [
    {
      "texto_verbatim": "Frecuencia de operación: 118-136.975 MHz, separación entre canales 25 kHz / 8.33 kHz",
      "referencia": "EETT_01_aclarada.md, Sección 4.2, PAGE 12"
    },
    {
      "texto_verbatim": "Potencia de salida mínima: 5W. Modulación AM compatible con sistemas aeronáuticos.",
      "referencia": "EETT_01_aclarada.md, Sección 4.2, PAGE 12"
    }
  ],
  "marcadores": [],
  "notas_bom": ""
}
```

**`estado_specs`**: marca el estado del ciclo de vida del archivo:
- `PENDIENTE_VERIFICACION` (estado inicial, salida del paso 2.5)
- `VERIFICADO` (después del paso 3.1)
- `REVISADO` (después del paso 3.2)

## Output derivado — `ITEM-{id}.md`

```markdown
---
item_id: IT-0001
nombre: "Radio base VHF digital"
tipo: BIEN
grupo: "Comunicaciones"
parent_id: ""
cantidad: "2"
unidad: "und"
referencia_eett: "EETT_01_aclarada.md, Sección 4.2, PAGE 12"
estado_specs: PENDIENTE_VERIFICACION
---

# IT-0001 — Radio base VHF digital

## Descripción

Radio base VHF digital — 2 und

## Requisitos extraídos en contexto (paso 2)

| # | Texto verbatim | Referencia |
|---|----------------|------------|
| 1 | Frecuencia de operación: 118-136.975 MHz, separación entre canales 25 kHz / 8.33 kHz | EETT_01_aclarada.md, Sección 4.2, PAGE 12 |
| 2 | Potencia de salida mínima: 5W. Modulación AM compatible con sistemas aeronáuticos. | EETT_01_aclarada.md, Sección 4.2, PAGE 12 |

> Estos requisitos serán verificados, normalizados y clasificados (hard/soft) en el paso 3.

## Notas del BOM

—
```

## Reglas

- No omitir ningún ítem del BOM (ni bienes ni servicios).
- No modificar ni enriquecer los datos. Transcripción fiel del BOM.
- Si un campo está vacío en el BOM, dejarlo vacío en el JSON.
- Mantener la coherencia de IDs: el `item_id` del archivo debe coincidir exactamente con el del BOM.

## Entrega

Escribí todos los archivos en `{OUTPUT_DIR}`.
Devolvé:
- `OK: {OUTPUT_DIR}`
- `Total archivos generados: {N} JSON + {N} MD (Bienes: {X}, Servicios: {Y})`
