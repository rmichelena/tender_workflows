# Extractor Benchmark — Documentos de Licitación

## Metodología

### Benchmark 1: Extractores sobre bases estándar (58 págs, documento mixto)

Documento: `bases_estandar_lp_bienes.pdf` — 58 páginas con mezcla de contenido digital y escaneado (firmas, stamps, páginas escaneadas). Este es el caso más representativo de documentos de licitación reales.

**Extractores probados**: LandingAI ADE (dpt-2-mini, dpt-2-latest), Google DocAI (v1.0, v1.5, v1.6), MarkItDown.

### Benchmark 2: Extractores sobre bases integradas (85 págs, escaneado puro)

Documento: `bases_integradas_rx.pdf` — 85 páginas 100% escaneadas.

### Benchmark 3: Comparativa inicial (329 páginas, 6 documentos)

6 documentos PDF de 3 licitaciones distintas. Todos procesados con MarkItDown y Google DocAI.

---

## Benchmark 1: Documento mixto (58 págs)

### Resultados por extractor

| Métrica | ADE dpt-2-latest | ADE dpt-2-mini | DocAI v1.6 | DocAI v1.0 | MarkItDown |
|---|---|---|---|---|---|
| **Tiempo** | 32s | 33s | 43 min | 42s | <5s |
| **Chars raw** | 196K | 177K | 122K | 125K | ~120K |
| **Chars clean** | 166K | 180K | 122K | 125K | ~120K |
| **Headings H2/H3** | 23/11 | 26/7 | 99/71 | 83/46 | ~20/10 |
| **Tablas** | 11 HTML | 29 HTML | 120 MD | 45 MD | ~20 MD |
| **Bullets** | 73 | 170 | 178 | 0 | ~50 |
| **OCR escaneado** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Firmas detectadas** | 160 | 0 | 0 | 0 | 0 |
| **Logos detectados** | 8 | 0 | 0 | 0 | 0 |
| **Créditos/costo** | 174 | 87 | ~$1.5 | ~$0.5 | gratis |

### OCR en zonas escaneadas

Todos los extractores con OCR (ADE, DocAI) capturan correctamente las specs técnicas en zonas escaneadas (ej: especificaciones de rodillos, equipos de rayos X). MarkItDown pierde completamente estas secciones.

### Post-procesamiento

El output de ADE requiere `clean_ade_output()` para eliminar:
- 160 attestations/firmas (bloques `<::attestation...::>`)
- 8 logos (bloques `<::logo...::>`)
- 44 decorations (círculos numerados, flechas, etc.)
- 4 NUL bytes → ° (degree symbol en "N°")

DocAI requiere `fix_ligatures()` para LaTeX artifacts (`\times`, `^{circ}`).

### Conclusión Benchmark 1

**LandingAI ADE dpt-2-latest es el mejor extractor para documentos mixtos**:
- 80x más rápido que DocAI v1.6 (32s vs 43 min)
- 36% más contenido que DocAI v1.6 (166K vs 122K chars)
- OCR funciona perfectamente en páginas escaneadas
- Detecta firmas/logos (se eliminan en post-proceso)
- Desventaja: menos estructura jerárquica (23 H2 vs 99 H2 de DocAI)

---

## Benchmark 2: Escaneado puro (85 págs)

| Extractor | Tiempo | Chars | Notas |
|---|---|---|---|
| DocAI v1.6-pro batch | 113 min | 186K | Mejor calidad |
| DocAI v1.6 batch | ~50 min | 188K | |
| DocAI v1.5 batch | ~45 min | 189K | Peor que v1.6 |
| DocAI v1.0 batch | ~3 min | 195K | Rápido, menos estructura |
| MarkItDown | <10s | 217K | No tiene OCR — texto es ruido |

Para documentos 100% escaneados, DocAI v1.6 sigue siendo la mejor opción si el tiempo no es problema. ADE no se probó en este documento.

---

## Benchmark 3: Comparativa inicial (329 páginas, 6 documentos)

### Por documento

| Documento | Págs | MarkItDown | DocAI Batch | Ratio |
|-----------|------|-----------|-------------|-------|
| clarifications_set4 | 6 | 15,701 | 12,152 | 0.77x |
| expediente_adp | 11 | 30,155 | 53,048 | 1.76x |
| pliego_absolutorio | 21 | 51,853 | 51,487 | 0.99x |
| bases_admin_rx | 85 | 213,623 | 195,756 | 0.92x |
| bases_integradas_rx | 85 | 217,200 | 195,495 | 0.90x |
| bases_adp | 121 | 367,025 | 845,155 | 2.30x |
| **TOTAL** | **329** | **895,557** | **1,353,093** | **1.51x** |

### Tiempos DocAI

| Documento | Págs | Modo | Tiempo |
|-----------|------|------|--------|
| clarifications_set4 | 6 | Batch | 87s |
| expediente_adp | 11 | Online | 48s |
| pliego_absolutorio | 21 | Online | 33s |
| bases_admin_rx | 85 | Online | 167s |
| bases_integradas_rx | 85 | Online | 162s |
| bases_adp | 121 | Batch | 420s (7 min) |
| bases_adp | 121 | Online | 784s (13 min) |

---

## Recomendaciones finales

| Tipo de documento | Extractor | Razón |
|---|---|---|
| **PDF mixto (digital + escaneado)** | LandingAI ADE `dpt-2-latest` | Mejor velocidad/calidad, OCR funciona |
| **PDF escaneado puro** | DocAI v1.6 batch | Máxima calidad de estructura |
| **PDF vectorial puro** | MarkItDown | Rápido, gratuito, suficiente |
| **DOCX sin gráficos críticos** | MarkItDown | Texto embebido, no necesita OCR |
| **DOCX con imágenes/diagramas** | Convertir a PDF → ADE | ADE no soporta DOCX directo |

### LandingAI ADE — Limitaciones conocidas

- **No soporta DOCX** (solo PDF, imágenes, XLSX, CSV)
- **No hay opción en el API** para filtrar logos/firmas/attestations — se eliminan con `clean_ade_output()`
- **Custom prompts** solo soporta tipo `figure` (no attestation/logo)
- **NUL bytes** aparecen ocasionalmente donde debería ir `°` — `clean_ade_output()` los corrige
- **Tablas en HTML** (no markdown nativo) — pueden necesitar conversión adicional
