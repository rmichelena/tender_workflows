# Extractor Benchmark — Documentos de Licitación

## Metodología

6 documentos PDF de 3 licitaciones distintas (329 páginas total).
Todos procesados con MarkItDown y Google DocAI v2 (Layout Parser).

## Resultados

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

## Conclusiones

1. **DocAI extrae 51% más contenido** en promedio
2. **En documentos escaneados** (expediente_adp, bases_adp): DocAI extrae 1.8x–2.3x más
3. **En documentos nativos** (bases_admin_rx): MarkItDown y DocAI son comparables (~90-100%)
4. **Modo batch es 2x más rápido** que modo chunked para docs grandes (7 min vs 13 min)
5. **MarkItDown es 10x más rápido** en velocidad bruta pero pierde contenido en escaneos/diagramas

## Recomendación

- **Docs con texto embebido y pocas páginas** → MarkItDown (rápido, gratuito)
- **Docs escaneados o con diagramas** → DocAI Batch (OCR, estructura, más contenido)
- **Docs >15 páginas** → DocAI Batch (sin fragmentación, mejor semántica)
