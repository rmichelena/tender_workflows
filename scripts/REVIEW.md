# Multi-Review B: `scripts/` etapa C

**Repo:** `rmichelena/tender_workflows`  
**Branch:** `main`  
**Commit reviewed:** `81f79b773315ad656e6f6a618de476a81fcbca0c`  
**Scope:** `scripts/` runtime pipeline etapa C  
**Bridge context:** `apps/portal/seace_monitor/analysis/tender_bridge.py`, `runner.py`  
**Excluded:** `ejemplos/`, `pdf_visual_diff_mvp/` experimental material  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, GLM-5.1, Qwen 3.6 Plus  
**Kimi K2.6:** returned no usable findings  
**Excluded by request:** security / hardening review

---

## Overall Assessment

The stage-C pipeline is reasonably structured and the `exit 23` visual-plan contract is a good design. The main risks are not architectural, but operational correctness issues:

- selected-document scope from the portal is not honored by stage C,
- source/artifact naming can silently drop or stale outputs,
- partial failures can produce complete-looking markdown or reuse corrupt artifacts,
- external extractor/tool waits lack consistent bounds,
- plan-page replacement has a few destructive edge cases.

No security/hardening findings are included.

---

## Findings

### H1. Stage C ignores the user-selected document list and processes every downloaded file
**Severity:** High  
**Files:** `apps/portal/seace_monitor/analysis/runner.py`, `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** GPT-5.5

`AnalysisRunner.analyze()` receives `selected_rel_paths` and passes them to the fast-reader branch, but the tender/stage-C branch calls `run_tender_stage1(config, proc_dir, documents_dir)` without the selected list. `prepare_tender_project()` then copies every file from `documents_dir` into the stage-C input folder.

**Impact:** if the user selects only a subset in the portal, stage C still analyzes unrelated downloaded documents, producing artifacts for the wrong corpus.

**Fix:** Thread `selected_rel_paths` through `run_tender_stage1()` and `prepare_tender_project()`. Copy only selected files, and clear `tender_project/inputs` before each run to avoid stale input leakage.

---

### H2. DOCX→PDF and original PDF with the same stem overwrite/skip each other
**Severity:** High  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** GPT-5.5, GLM

DOCX inputs are converted to `step_1_pdfs/{stem}.pdf`, while original PDFs are copied as `step_1_pdfs/{name}`. If inputs contain `Bases.docx` and `Bases.pdf`, both target `Bases.pdf`.

**Impact:** one source document is silently lost. With `--overwrite`, the original PDF overwrites the DOCX conversion; without overwrite, the original may be skipped.

**Fix:** Preflight for output-name collisions and fail clearly, or generate collision-safe names such as `{stem}__from_docx.pdf` and `{stem}__original.pdf`.

---

### H3. DocAI online chunk failures produce complete-looking markdown with missing pages
**Severity:** High  
**File:** `scripts/extractors/docai_online.py`  
**Flagged by:** DeepSeek

In chunked mode, failed chunks are recorded in JSON/stderr, but markdown is still written from successful chunks only. Missing page ranges are not represented inline.

**Impact:** downstream LLM processors that consume only the `.md` file see an apparently complete document with silent page gaps.

**Fix:** Insert explicit markdown placeholders for failed chunks, e.g. `## Páginas X-Y — ERROR DE EXTRACCIÓN`, and add a prominent top-level warning if any chunk failed.

---

### H4. `run_axis0_gemini()` has no context-window guard
**Severity:** High  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** DeepSeek

The bridge concatenates up to 12 markdown files × 80k chars into one prompt, potentially ~960k chars, without token estimation or trimming. Oversized prompts can fail opaquely.

**Impact:** large tender projects can abort the entire analysis after deterministic artifacts were already produced.

**Fix:** Estimate token budget and stop at ~80% of model context, logging included/skipped files. Better: split into multiple calls and aggregate.

---

### H5. Plan-page replacement defaults malformed `bbox_pct` to full-page region
**Severity:** High  
**File:** `scripts/pdf_plan_pages.py`  
**Flagged by:** DeepSeek

For `replace_images`, missing/malformed `bbox_pct` falls back to `[0, 0, 1, 1]`. That can resolve to a large/full-page region and apply destructive redaction/replacement.

**Impact:** malformed visual analysis JSON can white-out large parts of a page.

**Fix:** Require valid `bbox_pct` for replacement actions. Reject or skip replacements whose resolved region exceeds a safe page-area threshold. Validate analysis JSON before applying.

---

### H6. Replacement images use Helvetica/Base-14 and can lose Unicode before OCR
**Severity:** High  
**File:** `scripts/pdf_plan_pages.py`  
**Flagged by:** DeepSeek

Replacement images render LLM text via `fontname="helv"`, which is limited for non-Latin-1 glyphs. Characters outside the font encoding can render as tofu or disappear, then get embedded into the pre-OCR PDF.

**Impact:** technical observations/labels containing Unicode can be permanently lost before OCR.

**Fix:** Use a Unicode-capable rendering path (`insert_htmlbox`, embedded font, or normalization/fallback strategy) and add tests with accented symbols, curly quotes, and engineering symbols.

---

### H7. `xlsx_split` breaks cross-sheet formulas in single-sheet workbooks
**Severity:** High  
**File:** `scripts/extractors/xlsx_split.py`  
**Flagged by:** GLM

The splitter saves single-sheet workbooks with formulas preserved (`data_only=False`) after deleting sibling sheets. Cross-sheet formulas like `=Sheet2!A1` now reference missing sheets; later `data_only=True` analysis may return `None` and classify meaningful sheets as empty.

**Impact:** spreadsheet content can be lost/misclassified after splitting.

**Fix:** Replace cross-sheet formulas with cached values from a `data_only=True` workbook before saving single-sheet copies, or generate value-only copies for analysis. Emit manifest warnings for broken cross-sheet references.

---

### M1. `step_1_3_outputs.json` omits XLSX-normalized markdown artifacts
**Severity:** Medium  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** GPT-5.5

XLSX processing writes markdowns into `artifacts/step_1_normalizados`, but `step_1_3_outputs.json` records only markdown outputs produced from PDFs/Docling.

**Impact:** XLSX-only or XLSX-heavy projects produce valid markdown files that downstream consumers miss.

**Fix:** Accumulate all normalized markdown outputs, including XLSX conversions, with metadata (`source_path`, `source_type`, `sheet_name`, `extractor`).

---

### M2. LandingAI extractor can wait indefinitely
**Severity:** Medium  
**File:** `scripts/extractors/landingai_extract.py`  
**Flagged by:** GPT-5.5

The extractor polls until completion/failure with no max wait and downloads `output_url` with no timeout.

**Impact:** selected/batch LandingAI runs can block stage C indefinitely.

**Fix:** Add configurable `--max-wait`, `--poll-interval`, and URL download timeout. Emit a deterministic timeout status artifact and nonzero exit.

---

### M3. PyMuPDF documents are opened but not closed in plan/image audit paths
**Severity:** Medium  
**Files:** `scripts/pdf_plan_pages.py`, `scripts/pdf_image_audit.py`  
**Flagged by:** GLM, Qwen

Several functions open PyMuPDF documents without explicit close/context manager, including `pdf_plan_pages.audit_pdf()`, `build_outputs()`, and `pdf_image_audit.analyze_page_contents()`.

**Impact:** processing many large PDFs can leak file descriptors/native memory until GC runs.

**Fix:** Use `with fitz.open(...) as doc:` / `try/finally: doc.close()` for every document, including output docs (`pre`, `extracted`).

---

### M4. Exit 23 blocks unaffected PDFs when only some PDFs need visual analysis
**Severity:** Medium  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** GLM

If any PDF has visual candidates, the runner exits 23 before processing PDFs without candidates through build/Docling.

**Impact:** unaffected documents wait for visual resolution and must be reconsidered on rerun.

**Fix:** Process non-pending PDFs through build/Docling immediately, and exit 23 only for the pending subset. This likely requires per-PDF state rather than all-or-nothing orchestration.

---

### M5. Retry after exit 23 can reuse stale/corrupt partial artifacts
**Severity:** Medium  
**Files:** `apps/portal/seace_monitor/analysis/tender_bridge.py`, `scripts/run_step1_to_1_3.py`  
**Flagged by:** Qwen

After exit 23, the bridge retries with `overwrite=False`. The first run may have created partial PDFs/JSONs/candidate images. Existing files are accepted mostly via size checks.

**Impact:** corrupt or incomplete artifacts can be reused on retry.

**Fix:** On retry after exit 23, clear only planos-related artifacts or validate contents structurally: open PDFs with PyMuPDF, parse JSON, verify expected keys/pages.

---

### M6. `resolve_planos_pending()` can get stuck on missing/broken `audit_path`
**Severity:** Medium  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** DeepSeek

If the pending marker contains an `audit_path` that no longer exists, `read_text()` raises and the marker remains. Reruns hit the same broken marker.

**Impact:** stage-C analysis can fail repeatedly on a stale/bad marker.

**Fix:** Validate `audit_path.exists()` before reading. Skip/log broken items, move the marker aside, or remove it in a controlled failure path.

---

### M7. Modal Docling output naming changes depending on pre-OCR existence
**Severity:** Medium  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** GLM

The expected output path is based on `sanitize_filename(src)`, where `src` is either `*_clean.pdf` or `*_preocr.pdf`. Reruns can create multiple markdown outputs for the same logical source depending on whether pre-OCR exists.

**Impact:** stale/orphan markdowns and confusing downstream artifact discovery.

**Fix:** Use the logical source stem for output naming, not the transient source path: e.g. `{stem}_modal_docling.md`.

---

### M8. Redaction-based recurring drawing removal can still delete overlapping content
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GLM

`_strip_recurring_drawings()` uses redaction rectangles. Even with area limits, overlapping text/vector content inside the rect is removed.

**Impact:** decorative lines/frames near content can cause data loss.

**Fix:** Before redaction, check text spans and skip/shrink rects with overlap. Prefer reporting these drawings until content-stream surgery is implemented.

---

### M9. `xlsx_split` has avoidable workbook load/memory overhead
**Severity:** Medium  
**File:** `scripts/extractors/xlsx_split.py`  
**Flagged by:** DeepSeek

The splitter loads the workbook multiple times (`data_only=True`, `data_only=False`, then again per sheet).

**Impact:** large XLSX files incur heavy memory and parse overhead.

**Fix:** Reuse loaded workbooks where possible, or design a value-only extraction path for analysis. This is lower priority than preserving cross-sheet formula values.

---

### M10. `valid_file()` size checks are insufficient for binary/PDF validity
**Severity:** Medium  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** Qwen

The pipeline accepts some artifacts based on existence/size only.

**Impact:** interrupted runs can leave nonzero corrupt PDFs/JSONs that are reused.

**Fix:** Add validators per artifact type: PDF open/page count, JSON parse/schema, markdown nonempty plus expected header markers.

---

### L1. `step_1_3_blocked_modal_billing_limit.json` is declared but never written
**Severity:** Low  
**File:** `scripts/run_step1_to_1_3.py`  
**Flagged by:** Qwen

The filename is listed in cleanup/step metadata but no code writes it.

**Impact:** misleading operational contract.

**Fix:** Implement distinct billing-limit handling and marker write, or remove the dead entry.

---

### L2. Invalid `planos_mode` error is not descriptive
**Severity:** Low  
**File:** `apps/portal/seace_monitor/analysis/tender_bridge.py`  
**Flagged by:** GLM

Unsupported `planos_mode` raises a generic `ValueError`.

**Fix:** Validate early and raise a clear message listing supported values (`auto_leave`, `stop`).

---

## Recommended Fix Order

1. **H1** — honor portal-selected document subset in stage C.
2. **H2/H7/M1** — fix source/artifact naming and manifest completeness.
3. **H3/M10/M5** — prevent silent partial/corrupt output reuse and missing-page markdown.
4. **H4** — add Gemini context budget for axis0.
5. **H5/H6/M8** — harden plan-page replacement/destructive operations.
6. **H7/M9** — fix XLSX formula preservation and then optimize memory.
7. **M2/M3/M6/M7** — external wait bounds, resource cleanup, marker robustness, deterministic naming.
8. **L1/L2** — cleanup operational contract polish.

## Notes

No security/hardening review was performed. Findings focus on correctness, reliability, idempotency, artifact contracts, external-tool/API behavior, and downstream compatibility.
