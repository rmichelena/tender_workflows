# Carpeta `instrucciones/` — Workflow de Procurement para Licitación

Esta carpeta contiene el conjunto completo de prompts, parámetros y catálogos que un agente orquestador debe cargar para ejecutar de extremo a extremo un proceso de procurement: desde EETT/aclaraciones hasta un shortlist consolidado con matrices de cumplimiento.

## Cómo se usa

1. Apuntá un agente orquestador (modelo recomendado: GPT-5.5, Gemini 3.1 Pro o Kimi K2.6) a esta carpeta.
2. Indicale dónde está la carpeta del proyecto (con `inputs/`, donde viven EETT, anexos y aclaraciones).
3. El orquestador leerá `00_prompt_orquestador.md` y desde ahí descubrirá todo el resto.

## Mapa de archivos

```
instrucciones/
├── README.md                              ← este archivo
├── 00_prompt_orquestador.md               ← punto de entrada del orquestador
├── 01_workflow.md                         ← runbook operativo (pasos 1→7)
├── params.yaml                            ← timeouts, reintentos, batch, combos
├── catalog_modelos.md                     ← modelos disponibles + funciones permitidas
├── catalog_tools.md                       ← search/fetch providers + reglas de uso
├── formato_matriz_cumplimiento.md         ← formato obligatorio de matriz por candidato
├── prompts/                               ← plantillas para subagentes
│   ├── prompt_ocr_vision.md
│   ├── prompt_planos_vision.md
│   ├── prompt_document_indexer.md
│   ├── prompt_merge_aclaraciones_ejecutor.md
│   ├── prompt_merge_aclaraciones_auditor.md
│   ├── prompt_bom_highlevel.md
│   ├── prompt_bom_exploded.md
│   ├── prompt_item_pack_from_bom.md
│   ├── prompt_specs_verificacion_herencia.md
│   ├── prompt_specs_revisor.md
│   ├── prompt_bom_para_busqueda.md
│   ├── prompt_item_manager.md
│   ├── prompt_search_worker.md
│   ├── prompt_consolidacion_paso7.md
│   └── prompt_QA_final.md
└── schemas/                               ← contratos JSON (canónicos máquina)
    ├── plan_pages_analysis.schema.json
    ├── document_index.schema.json
    ├── bom_item.schema.json
    ├── item_specs.schema.json
    ├── candidato_cumplimiento.schema.json
    └── consolidado_row.schema.json
```

## Convenciones de formato

| Tipo de artefacto | Formato canónico (que produce el agente) | Derivados (auto, para humano) |
|---|---|---|
| EETT y aclarados (paso 1) | Markdown | — |
| Planos/diagramas extraídos (paso 1.2b) | JSON + PDF extraído + PDF pre-OCR | MD |
| Índice estructural (paso 1.5) | JSON | MD |
| Lecturas temáticas Paso 2A | JSON (subagente) | MD (orquestador) |
| Chunk plans Paso 2A | JSON (orquestador) | — |
| BOM (HL, exploded, búsqueda) | JSON | TSV + MD |
| Item pack (paso 2.5) | JSON por ítem | MD por ítem |
| Specs por ítem (paso 3) | JSON por ítem | MD por ítem |
| Resultados de búsqueda (paso 6) | JSON por ítem | MD por ítem |
| Matrices de cumplimiento | JSON por candidato | MD por candidato |
| Consolidado final (paso 7) | JSON | TSV + MD + XLSX |
| Reportes (auditoría, QA, log) | Markdown | — |
| Configuración (overlay, params) | YAML | — |

Regla: cuando un subagente produce JSON, el orquestador genera los derivados automáticamente (la conversión es trivial y determinista).

## Estructura esperada de la carpeta de proyecto

```
proyecto/
├── inputs/                          (EETT, anexos, aclaraciones — read-only)
├── overlay_usuario.yaml             (preferencias de búsqueda capturadas post-BOM / Gate 5)
├── artifacts/
│   ├── step_1_pdfs/                 (PDFs preservados / DOCX→PDF)
│   ├── step_1_pdfs_clean/           (PDFs optimizados con `--strip` + reportes `{stem}_clean_report.json` y `{stem}_clean_page_analysis.json`)
│   ├── step_1_planos/               (planos detectados/análisis visual; paso default)
│   ├── step_1_pdfs_preocr/          (PDFs con planos sustituidos por texto)
│   ├── step_1_normalizados/         (markdowns post-Modal-Docling/preocr)
│   ├── step_1_axis0_preindex/       (`axis0_go_no_go_summary.md`; gate humano antes de indexar)
│   ├── step_1_aclaradas/            (docs aclarados + auditoría)
│   ├── step_1_index/                (`{stem}_index.json/.md`; índice estructural post-gate)
│   ├── step_1_repaired/             (opcional; Markdown reparado, patch y log)
│   ├── step_2_chunks/               (`{stem}_chunks.json`; chunks determinísticos por índice)
│   ├── step_2_thematic/             (lecturas temáticas JSON canónico + MD derivado)
│   ├── step_2_bom/                  (consolidados HL y exploded posteriores a ledgers temáticos)
│   ├── step_2_5_items/              (1 JSON+MD por ítem, base estructurada)
│   ├── step_3_specs/                (ítems verificados, con herencia resuelta + revisión)
│   ├── step_4_busqueda/             (BOM búsqueda — solo bienes, sin cantidades)
│   └── step_6_resultados/
│       ├── items/                   (resultado por ítem en JSON+MD)
│       └── matrices/ITEM-XXX/       (matrices por candidato en JSON+MD)
├── outputs/
│   ├── consolidado.json             (canónico)
│   ├── consolidado.tsv              (derivado tabular)
│   ├── consolidado.md               (derivado legible)
│   ├── consolidado.xlsx             (derivado Excel)
│   └── QA_report.md
└── logs/
    └── decision_log.md              (modelos usados, reintentos, escalamientos)
```

## Paso 2A schemas por eje

Además del shape común `schemas/thematic_extraction.schema.json`, usar contratos especializados por eje:

- `schemas/axis_0_main_tender_data.schema.json`
- `schemas/axis_1_proposal_signature_documents.schema.json`
- `schemas/axis_2_execution_documentary_deliverables.schema.json`
- `schemas/axis_3_execution_services_obligations.schema.json`
- `schemas/axis_4_goods_licenses_equipment.schema.json`

Los schemas por eje restringen `entry_type`, `phase` y `source_context_type` a valores semánticamente válidos para cada tarea.
