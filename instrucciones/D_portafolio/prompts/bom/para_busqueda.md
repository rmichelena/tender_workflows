# Prompt — Generación del BOM para Búsqueda (Paso 4) + QA — v0.2

> **Tipo**: tarea principalmente determinística del orquestador (filtrado + limpieza). Sin embargo, la traducción de referencias normativas locales a estándares internacionales (ej. "norma DGAC" → "ICAO Annex X" si discernible) requiere razonamiento técnico — para esos casos puntuales puede invocarse una LLM call one-shot delegada.

A partir del BOM Exploded consolidado y los `ITEM-{id}_specs.json` ya verificados (Paso 3), producí una versión simplificada para que los search workers del Paso 6 puedan trabajar sobre cada item sin contexto local peruano.

## Reglas v0.2

1. **Determinístico cuando posible**: filtrado de servicios, eliminación de cantidades, etc. = script Python.
2. **LLM call selectiva** solo para traducir referencias normativas no obvias (las obvias el script las maneja con un diccionario).
3. **QA interno obligatorio** antes de cerrar (ver §QA).
4. **Output con schema fuerte**: el resultado se valida.

## Inputs

- `{BOM_EXPLODED_JSON}`: BOM exploded consolidado en JSON.
- `{ITEMS_SPECS_DIR}`: directorio con todos los `ITEM-{id}_specs.json`.
- `{OUTPUT_PATH_JSON}`: ruta donde escribir el BOM búsqueda en JSON canónico.

## Reglas

1. **Filtrar bienes únicamente**: descartar todos los ítems con `tipo: SERVICIO`. Los servicios reaparecerán en el consolidado del paso 7 como filas sin candidato.
2. **Quitar cantidades**: eliminar `cantidad` y `unidad`. Lo que importa para la búsqueda es la especificación, no cuántos se necesitan.
3. **Limpiar referencias no-buscables**: identificar y reemplazar/aclarar referencias a normativa local, autoridades, sistemas o redes del cliente que un agente de búsqueda externo no podría reconocer. Ejemplos:
   - "según autorización MTC" → reemplazar por la frecuencia o estándar técnico equivalente.
   - "compatible con Redap Corpac" → reemplazar por el protocolo o especificación técnica que esa compatibilidad implica.
   - "según normativa DGAC" → traducir al estándar internacional aplicable (ej. ICAO Annex X) si es discernible; si no, marcar para revisión.
4. **Mantener specs como parámetros técnicos buscables**: las specs deben quedar en términos que un agente con acceso web pueda usar (frecuencia, potencia, IP rating, certificación CE/FCC/UL si aplica, etc.).

## Output canónico — `BOM_busqueda.json`

```json
{
  "tipo": "BOM_BUSQUEDA",
  "fuente": "BOM_exploded_consolidado.json + step_3_specs/",
  "items": [
    {
      "item_id": "IT-0001",
      "nombre": "Radio base VHF digital",
      "grupo": "Comunicaciones",
      "parent_id": "",
      "spec_clave_resumida": "VHF aeronáutica 118-136.975 MHz, AM, 5W mín, conector tipo N, separación 25/8.33 kHz",
      "ruta_specs": "artifacts/step_3_specs/ITEM-IT-0001_specs.json",
      "limpieza_aplicada": [],
      "marcadores": []
    },
    {
      "item_id": "IT-0007",
      "nombre": "Detector de metales tipo arco",
      "grupo": "Seguridad",
      "parent_id": "",
      "spec_clave_resumida": "Arco detector de metales para personas, ancho pasaje ≥0.76m, alto ≥2.03m, IP54, cumplimiento de exposición humana",
      "ruta_specs": "artifacts/step_3_specs/ITEM-IT-0007_specs.json",
      "limpieza_aplicada": [
        "Reemplazado 'según norma local DGSE' por 'según normas IEC 62222 / NIJ 0601.03 (estándar internacional equivalente)'"
      ],
      "marcadores": []
    }
  ],
  "items_excluidos_por_servicio": ["IT-0003", "IT-0009", "IT-0015"],
  "qa": {
    "sin_servicios_remanentes": "OK",
    "sin_cantidades": "OK",
    "sin_referencias_no_buscables": "OK",
    "todos_los_bienes_presentes": "OK"
  }
}
```

**Notas**:
- `spec_clave_resumida`: 1-2 líneas con los 3-5 parámetros más diferenciadores. Útil para que `search/search_worker.md` tenga un "hook" rápido. La lista completa de requerimientos vive en `ruta_specs`.
- `ruta_specs`: el subagente-item del paso 6 carga este archivo para tener todos los requisitos verbatim.
- `limpieza_aplicada`: registro de qué referencias no-buscables se modificaron y cómo.
- `items_excluidos_por_servicio`: lista de IDs filtrados (servicios). Se conservan para que el paso 7 los reincluya en el consolidado final.

## Output derivado — `BOM_busqueda.tsv`

Tabla TSV (separada por tabs) con columnas:
- `item_id`
- `nombre`
- `grupo`
- `parent_id`
- `spec_clave_resumida`
- `ruta_specs`

## QA interno (autoejecutado)

Antes de cerrar, verificá:
1. **Sin servicios**: ningún ítem en `items` tiene `tipo: SERVICIO` (cruzar con BOM exploded).
2. **Sin cantidades**: ningún campo `cantidad` o `unidad` en los ítems.
3. **Sin referencias no-buscables**: para cada `spec_clave_resumida`, buscar palabras como "MTC", "DGAC", "Corpac", "Redap", "DGSE" u otras siglas locales; si quedan, agregar a `limpieza_aplicada` o reportar.
4. **Completitud**: el conteo de bienes en `items` + servicios en `items_excluidos_por_servicio` = total de ítems del BOM exploded.

Si algún check falla, marcar el campo correspondiente en `qa` como `FALLA: {detalle}` y reportar al humano antes de continuar.

## Entrega

Escribí el resultado en `{OUTPUT_PATH_JSON}` (y derivado `.tsv`).
Devolvé:
- `OK: {OUTPUT_PATH_JSON}`
- `Bienes a buscar: {N} | Servicios excluidos: {S} | Total BOM exploded: {T}`
- `QA: OK | FALLA: {detalle}`
