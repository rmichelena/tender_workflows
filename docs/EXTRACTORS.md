# Extractores y pipeline documental (etapa C)

Documentación técnica del **Paso C.1** (normalización PDF/XLSX → Markdown).  
Extraída del README legacy `tender_procurement`; el producto completo empieza en [STAGES.md](STAGES.md).

---

## Alcance

| Componente | Path |
|------------|------|
| Runner etapa C | `scripts/run_step1_to_1_3.py` |
| Extractores | `scripts/extractors/` |
| Planos / cleaning | `scripts/pdf_image_audit.py`, `scripts/pdf_plan_pages.py` |
| Índice (C.4) | `scripts/render_document_index_md.py` |
| Config operativa | `instrucciones/shared/params.yaml` → `step_1_defaults` |

Puente desde portal: `apps/portal/seace_monitor/analysis/tender_bridge.py`.

---

## Quick start

```bash
cp scripts/extractors/extractors.conf.example scripts/extractors/extractors.conf
python3 -m venv .venv && . .venv/bin/activate
pip install requests PyMuPDF google-auth-oauthlib markitdown landingai-ade

.venv/bin/python scripts/extractors/modal_docling_extract.py documento.pdf output/
```

Convención PDF limpio: `artifacts/step_1_pdfs_clean/{stem}_clean.pdf`.

---

## Flujo default C.1

1. Triage por tipo (PDF / DOCX / XLSX).
2. Cleaning con `--strip` + page analysis.
3. Detección planos (`pdf_plan_pages.py` + prompt visual si exit 23).
4. Modal Docling sobre `{stem}_preocr.pdf` o `{stem}_clean.pdf`.
5. Sin fallback silencioso a otro extractor.

Runbook: [instrucciones/C_conversion/01_runbook.md](../instrucciones/C_conversion/01_runbook.md).

---

## Extractores disponibles

| Extractor | Uso |
|-----------|-----|
| **Modal Docling** ⭐ default workflow | `modal_docling_extract.py` |
| Docling local | `docling_extract.py` |
| LandingAI ADE | PDF escaneado, fallback autorizado |
| MarkItDown | PDF vectorial / pruebas rápidas |
| Google DocAI | Calidad máxima, lento |
| Rama XLSX | `xlsx_split.py`, `xlsx_sheet_analyze.py`, `xlsx_convert.py` |

Guías API: `scripts/extractors/api guide references/`  
Benchmark: `scripts/extractors/extractor_benchmark.md`  
DocAI setup: `scripts/extractors/docai_setup.md`

---

## Guía rápida de selección

| Documento | Default |
|-----------|---------|
| Flujo etapa C completo | strip + planos + Modal Docling |
| PDF escaneado | Modal Docling (pre-OCR) |
| PDF vectorial | Modal Docling |
| DOCX | LibreOffice → PDF → flujo PDF |
| XLSX | Rama nativa openpyxl |

---

## Post-procesadores

En `scripts/extractors/common.py`: `clean_ade_output()`, `fix_ligatures()`, `fix_chunk_spacing()`, `sanitize_filename()`.

---

## Salida

- `{nombre}_{extractor}.md` + `.json` según extractor.
- Artifacts bajo `portafolio/artifacts/step_1_*` (layout objetivo).

Para temática y BOM ver etapa D: [instrucciones/D_portafolio/README.md](../instrucciones/D_portafolio/README.md).
