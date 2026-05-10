## Lista final de entregables

1. `00_prompt_orquestador.md`
   Prompt breve de entrada: indica rutas de carpetas (proyecto, instrucciones), ordena leer workflow + params + catálogos, hacer plan, pedir inputs humanos iniciales, y ejecutar. Incluye instrucciones de gates/pausas.

2. `01_workflow.md`
   Documento detallado del flujo paso a paso (1→7), incluyendo:
   - Inputs/outputs de cada paso
   - Qué subagentes lanza cada paso (nivel, cantidad)
   - Criterios de "done" por paso
   - Gates humanos (docs simples/complejos, aprobación aclaradas, escalamiento items sin candidato)
   - El paso determinista "BOM exploded → item markdown pack"

3. `params.yaml`
   - Timeouts: búsqueda=600s, subagente-item=1500s, default=300s
   - `max_relanzamientos_item: 2`
   - `batch_size: 3`
   - Reintentos por tool
   - Regla de rotación: no repetir mismo modelo+tool en retry

4. `catalog_modelos.md`
   Tabla de modelos disponibles con: capacidades (visión, reasoning, contexto), fortalezas, y funciones permitidas/prohibidas por modelo. El orquestador decide desde aquí respetando diversidad.

5. `catalog_tools.md`
   Search/fetch providers (Firecrawl, Tinyfish, Brave, Perplexity) con características, casos de uso, y reglas de diversidad.

6. `formato_matriz_cumplimiento.md`
   Reglas de formato de la matriz (4 columnas: Requerimiento verbatim, Cumplimiento ✅/⚠️/❌, Especificación del equipo, Referencias), criterios para cada emoji, ejemplo completo.

7. `prompts/` (carpeta con plantillas parametrizadas):
   - `prompt_ocr_vision.md`
   - `prompt_merge_aclaraciones_ejecutor.md`
   - `prompt_merge_aclaraciones_auditor.md`
   - `prompt_bom_highlevel.md`
   - `prompt_bom_exploded.md`
   - `prompt_specs_herencia.md`
   - `prompt_specs_revisor.md`
   - `prompt_item_pack_from_bom.md` (determinista, BOM→markdowns individuales)
   - `prompt_item_manager.md` (paso 6: lanza búsquedas, valida, itera, produce matriz de cumplimiento)
   - `prompt_search_worker.md` (paso 6: búsqueda con evidencia, no EOL)
   - `prompt_consolidacion_paso7.md`
   - `prompt_QA_final.md`

8. `schemas/` (contratos de salida, opcional pero recomendado):
   - Estructura del item markdown
   - Formato CSV/long del consolidado final
   - Estructura del BOM en cada etapa

---

Total: 8 entregables (algunos son carpetas con múltiples archivos).