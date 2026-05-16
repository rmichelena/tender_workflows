# Roadmap / Observaciones sueltas

Notas vivas del workflow `tender_procurement`. Este archivo captura hallazgos que no ameritan implementación inmediata, decisiones de diseño y mejoras candidatas.

## Paso 1.2b — Planos / diagramas pre-OCR

### Texto de reemplazo para imágenes

- Las regiones `replace_images` deben reemplazarse como **imagen renderizada** dentro del rectángulo real de la imagen original, no como overlay de texto PDF.
- El rectángulo usado para redaction/replacement debe venir del **rendered final rect** de PyMuPDF (`page.get_text("dict")`, blocks `type=1`, o equivalente), no del bbox aproximado del modelo visual.
- El `bbox_pct` del modelo visual debe tratarse como locator aproximado, nunca como geometría destructiva final.
- Evitar márgenes arbitrarios en redactions: pueden borrar texto real alrededor.
- Guardar PDFs generados con compresión/garbage collection (`garbage=4`, `deflate=True`, `deflate_images=True`, `deflate_fonts=True`, `clean=True`).

### Formato OCR-friendly del texto renderizado

Objetivo: que Docling/OCR lea el bloque como texto plano descriptivo, sin promoverlo a headings Markdown ni introducir jerarquía falsa.

Preferir:

```text
[imagen reemplazada] ver planos_extraidos
[resumen] texto corto...
[texto visible] códigos principales...
[notas] observaciones técnicas...
```

Evitar:

```text
4.12 Topología del aeropuerto...
Códigos/texto visible:
Información técnica visible:
```

Motivo: líneas que arrancan con numeración formal (`4.12`, `Partida 3.00`) o parecen títulos pueden ser promovidas por Docling a `##`/`###` aunque solo sean metadata de sustitución visual.

## Paso 1.5 — Índice estructural

### Correcciones Markdown sugeridas

Las correcciones sugeridas por el indexador son útiles como QA, pero no todas deben aplicarse automáticamente.

Aplicación automática solo cuando:

- el patrón sea determinístico;
- conserve texto exactamente;
- sea puramente estructural;
- no cambie numeración oficial ni interpretación legal/técnica.

Ejemplos candidatos a auto-apply:

- bajar `##` a `###` cuando claramente son subtítulos internos promovidos por Docling;
- consolidar heading duplicado consecutivo que refiere a la misma sección;
- eliminar/reducir headers de tabla repetidos cuando el patrón exacto es verificable.

No auto-apply:

- renumeraciones de secciones (`1.1 → 7.1`, `8.1 → 9.1`);
- cambios que podrían corregir un error del documento original, no del extractor;
- reparaciones semánticas o inferidas.

### Campos de schema considerados

No agregar `page_start/page_end` inferidos por LLM. Si el Markdown no conserva marcas de página, el modelo no puede saber páginas de forma confiable.

Si se necesitan páginas, deben venir de upstream:

- Docling con page markers reales;
- inyección determinística de `<!-- page: N -->` durante parsing;
- mapa externo `line_range → page_number`.

Campos que sí podrían agregarse después:

- `source_line_start` / `source_line_end`: verificables desde Markdown.
- `duplicate_of_section_id`: útil para contenido repetido entre Bases/Contrato o tablas duplicadas.
- `markdown_artifact_type`: para clasificar artifacts de Docling, OCR o reemplazos visuales.
- `repair_confidence` / `repair_scope`: separar reparación determinística segura de inferencia semántica.
- `source_page_start` / `source_page_end`: solo si los provee el pipeline, no si los infiere el LLM.

### Paso 1.5 — Evitar extracción factual accidental

Hallazgo: el resumen Markdown del índice puede tender a incluir datos factuales (montos, plazos, requisitos de consorcio, cantidades) porque el schema tiene campos `summary`/`notes` y el prompt pide un resumen humano.

Decisión:

- El índice estructural no debe convertirse en mini-resumen del documento.
- `summary` debe describir la función estructural de la sección, no sus datos.
- Evitar valores específicos salvo que sean necesarios para distinguir una sección/tabla.

Ejemplo preferido:

```text
8. Monto Máximo — sección de presupuesto/monto referencial
2. Condiciones de Consorcios — sección de requisitos de participación en consorcio
```

Evitar:

```text
8. Monto Máximo — US$ 1,327,351.76 incl. IGV
2. Condiciones de Consorcios — máximo 2 integrantes, mínimo 40% participación
```

Si después se necesita extracción factual, debe ser otro paso con schema propio y control de evidencia.

## Paso 2A — Chunk plan determinístico

Decisión: los subagentes temáticos no deben inventar ventanas de lectura. El orquestador precomputa un `{basename}_chunks.json` por documento a partir del índice estructural de Paso 1.5.

Reglas:

- Target aproximado: 500 líneas.
- Cortar preferentemente en boundaries de sección/numeral del índice.
- Si una sección grande no tiene subdivisión fina, se conserva como chunk grande antes que cortar arbitrariamente.
- Gaps pequeños del índice se absorben en chunks vecinos para garantizar cobertura total del Markdown.
- El subagente recibe `chunk_plan_path` y debe leer `chunks[]` en orden.

Campos útiles del chunk plan:

- `chunk_id`
- `line_start` / `line_end`
- `section_ids`
- `section_titles`
- `split_from_section_id` / `split_part` si hubo split mecánico
- `coverage.missing_ranges` debe quedar vacío para documentos listos para Paso 2A.
