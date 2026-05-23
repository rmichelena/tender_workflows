# Prompt — Consolidación Final (Paso 7.1) — v0.2

> **Tipo**: principalmente tarea determinística del orquestador (agregación + transformación de schemas). El JSON canónico se construye con script. Las derivaciones a TSV/MD/XLSX también son determinísticas.
>
> **LLM call selectiva** solo para `spec_clave` (resumen 1-línea de 2-3 requisitos Hard más diferenciadores) — esa síntesis sí necesita razonamiento.

Producir el consolidado final unificando el BOM de búsqueda + BOM exploded (para servicios) + resultados de búsqueda + matrices de cumplimiento.

## Reglas v0.2

1. **Output canónico = JSON**; derivados TSV/MD/XLSX se generan determinísticamente.
2. **Schema validation**: el JSON canónico se valida contra `instrucciones/D_portafolio/schemas/consolidado_row.schema.json`.
3. **Completitud**: todos los items del BOM Exploded (bienes + servicios) deben aparecer.
4. **Items SIN_CANDIDATO y SERVICIOS**: incluir con `candidato_num: 0` (no omitir).
5. **`datasheet_source` y `evidence_quality`** se propagan desde la matriz al consolidado para filtros downstream.

## Inputs

- `{BOM_BUSQUEDA_JSON}`: BOM para búsqueda (paso 4) — lista maestra de bienes a buscar.
- `{BOM_EXPLODED_JSON}`: BOM exploded consolidado (para incluir servicios en el consolidado final).
- `{RESULTADOS_DIR}`: directorio con todos los `ITEM-{id}_resultado.json` (paso 6).
- `{MATRICES_DIR}`: directorio con matrices de cumplimiento por candidato (`ITEM-{id}_candidato_{n}_*.json`).
- `{OUTPUT_JSON}`: ruta del consolidado canónico.
- `{OUTPUT_TSV}`, `{OUTPUT_MD}`, `{OUTPUT_XLSX}`: rutas de derivados.

## Estructura de la tabla consolidada (formato "long")

Una entrada por cada combinación (ítem, candidato). Conforme a `instrucciones/D_portafolio/schemas/consolidado_row.schema.json`. Campos:

| Campo | Contenido |
|-------|-----------|
| `item_id` | ID del ítem |
| `nombre` | Nombre/descripción del ítem |
| `grupo` | Clasificación/grupo |
| `tipo` | BIEN / SERVICIO |
| `spec_clave` | Resumen de los 2-3 requisitos Hard más diferenciadores |
| `candidato_num` | Número del candidato (1, 2, 3…). 0 si SIN_CANDIDATO o si es servicio. |
| `marca`, `modelo`, `part_number` | Datos del candidato (vacíos si SIN_CANDIDATO o servicio) |
| `estado` | VALIDO / CONDICIONADO / SIN_CANDIDATO / SERVICIO |
| `url_fabricante` | URL del producto en sitio del fabricante |
| `url_datasheet` | URL del datasheet/manual |
| `resumen_cumplimiento` | "{cumple}OK / {parcial}PARCIAL / {nocumple}NO_CUMPLE" (vacío si SIN_CANDIDATO/SERVICIO) |
| `notas` | Observaciones: condiciones, qué falta, riesgos, alternativas, diagnóstico si SIN_CANDIDATO |
| `ruta_matriz` | Ruta al archivo de matriz JSON canónica de este candidato (vacío si SIN_CANDIDATO/SERVICIO) |
| `ruta_resultado` | Ruta al `ITEM-{id}_resultado.json` (siempre, para auditoría) |

## Reglas de inclusión

1. **Bienes**: para cada ítem en `BOM_BUSQUEDA_JSON`, leer su `ITEM-{id}_resultado.json`. Generar:
   - Una fila por cada candidato VÁLIDO (ordenados por `candidato_num`).
   - Una fila por cada candidato CONDICIONADO.
   - Si el ítem quedó SIN_CANDIDATO: una fila con `candidato_num: 0`, `estado: SIN_CANDIDATO`, campos de candidato vacíos, y `notas` con el diagnóstico.

2. **Servicios**: para cada ítem `tipo: SERVICIO` en `BOM_EXPLODED_JSON`, generar **una fila** con `candidato_num: 0`, `estado: SERVICIO`, campos de candidato vacíos, y `notas: "Servicio — no requiere búsqueda de producto"`.

3. **Orden**: agrupar por `grupo`, luego por `item_id`, luego por `candidato_num`.

4. **Completitud**: TODOS los ítems del BOM exploded deben aparecer en el consolidado, sin excepción.

5. **Consistencia**: los datos (marca, modelo, PN, estado) deben coincidir exactamente con lo que dice el `ITEM-{id}_resultado.json` correspondiente. No interpretar ni modificar.

6. **`spec_clave`**: extraer del `ITEM-{id}_specs.json` los 2-3 parámetros Hard más distintivos (frecuencia, potencia, IP, dimensiones críticas, etc.). 1 línea, 80 caracteres aprox.

## Output canónico — `consolidado.json`

```json
{
  "tipo": "CONSOLIDADO_FINAL",
  "fecha_generacion": "2026-05-10",
  "totales": {
    "items_total": 87,
    "items_bienes": 62,
    "items_servicios": 25,
    "items_resueltos": 58,
    "items_solo_condicionados": 3,
    "items_sin_candidato": 1,
    "candidatos_totales": 142
  },
  "filas": [
    {
      "item_id": "IT-0007",
      "nombre": "Detector de metales tipo arco",
      "grupo": "Seguridad",
      "tipo": "BIEN",
      "spec_clave": "Arco detector personas, ancho ≥0.76m, IP54, NIJ 0601.03",
      "candidato_num": 1,
      "marca": "CEIA",
      "modelo": "HI-PE Plus",
      "part_number": "HI-PE-PLUS-STD",
      "estado": "VALIDO",
      "url_fabricante": "https://www.ceia.net/security/product/HI-PE-Plus",
      "url_datasheet": "https://www.ceia.net/.../HIPEPlusbrochureE.pdf",
      "resumen_cumplimiento": "4 OK / 1 PARCIAL / 0 NO_CUMPLE",
      "notas": "Garantía no especificada en documentación pública; confirmar con representante.",
      "ruta_matriz": "artifacts/step_6_resultados/matrices/ITEM-IT-0007/ITEM-IT-0007_candidato_1_CEIA_HI-PE-Plus.json",
      "ruta_resultado": "artifacts/step_6_resultados/items/ITEM-IT-0007_resultado.json"
    },
    {
      "item_id": "IT-0009",
      "nombre": "Capacitación al personal operador",
      "grupo": "Servicios",
      "tipo": "SERVICIO",
      "spec_clave": "",
      "candidato_num": 0,
      "marca": "",
      "modelo": "",
      "part_number": "",
      "estado": "SERVICIO",
      "url_fabricante": "",
      "url_datasheet": "",
      "resumen_cumplimiento": "",
      "notas": "Servicio — no requiere búsqueda de producto",
      "ruta_matriz": "",
      "ruta_resultado": ""
    },
    {
      "item_id": "IT-0042",
      "nombre": "Antena específica banda L estrecha",
      "grupo": "Comunicaciones",
      "tipo": "BIEN",
      "spec_clave": "Antena banda L 1565-1585 MHz, 8 dBi, IP67",
      "candidato_num": 0,
      "marca": "",
      "modelo": "",
      "part_number": "",
      "estado": "SIN_CANDIDATO",
      "url_fabricante": "",
      "url_datasheet": "",
      "resumen_cumplimiento": "",
      "notas": "DIAGNÓSTICO: combinación de banda L estrecha + ganancia ≥8 dBi en formato compacto descarta toda la oferta mainstream tras 3 rondas. Sugerencia: relajar ganancia a ≥6 dBi permitiría incluir Antcom y Tallysman.",
      "ruta_matriz": "",
      "ruta_resultado": "artifacts/step_6_resultados/items/ITEM-IT-0042_resultado.json"
    }
  ]
}
```

## Output derivado — `consolidado.tsv`

TSV (separado por tabs) con los mismos campos que el JSON, una fila por entrada del array `filas`. Cabecera con nombres de campo.

## Output derivado — `consolidado.md`

Tabla markdown con las mismas columnas. Si es muy ancha, dividir en dos tablas vinculadas por `item_id` (tabla principal con datos de candidato + tabla complementaria con notas y rutas).

## Output derivado — `consolidado.xlsx`

- **Hoja "Consolidado"**: tabla plana con filtros automáticos. NO usar celdas combinadas (merged cells).
- **Hoja "Resumen"**: bloque con los `totales` (ítems totales, resueltos, condicionados, sin candidato, etc.) + tabla de conteo por grupo.

## Procedimiento

1. Leer `BOM_busqueda.json` y `BOM_exploded_consolidado.json`.
2. Construir el orden de ítems (bienes primero por grupo, luego servicios).
3. Para cada bien: leer su `ITEM-{id}_resultado.json` y generar las filas de candidatos.
4. Para cada servicio: generar una fila SERVICIO.
5. Para cada ítem leer su `ITEM-{id}_specs.json` y extraer `spec_clave`.
6. Verificar conteos contra los `totales` esperados.
7. Escribir JSON canónico.
8. Generar derivados TSV, MD, XLSX (conversión determinista a partir del JSON).

## Entrega

Escribí los archivos en las rutas indicadas.
Devolvé:
- `OK: {OUTPUT_JSON}, {OUTPUT_TSV}, {OUTPUT_MD}, {OUTPUT_XLSX}`
- Resumen: `{N} ítems | {V} con candidatos válidos | {C} solo condicionados | {S} sin candidato | {T} candidatos totales | {SV} servicios`
