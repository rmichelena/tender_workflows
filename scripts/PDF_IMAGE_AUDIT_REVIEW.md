# Code Review: `scripts/pdf_image_audit.py`

**Date:** 2026-05-14  
**Reviewers:** GPT-5.5, DeepSeek V4 Pro, Kimi K2.6, GLM-5.1  
**Scope:** `scripts/pdf_image_audit.py` only  
**Excluded by request:** Security review  
**Qwen:** timed out before returning findings  

---

## Overall Assessment

The script has useful audit/reporting capabilities, but the destructive `--strip` path is too aggressive for production use on tender PDFs without additional guardrails.

Main risk: several removal paths can delete legitimate document content, not just logos/watermarks/noise. Audit and extract modes are much safer than strip mode.

**Recommended stance:**
- ✅ Use `audit` freely.
- ✅ Use `extract` with caution and test output.
- ⚠️ Treat `--strip` as experimental until the High findings below are fixed.

---

## Findings

### H1. Duplicate/perceptual duplicate stripping deletes the keeper too
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5

`DUPLICATE` and `PERCEPTUAL_DUP` candidates compute `keep_xref` / `drop_xrefs`, but `_strip_images_content_stream()` strips every xref in `c["xrefs"]`. That removes both duplicates and the intended keeper.

**Impact:** identical content images/photos can be fully removed instead of deduplicated.

**Fix:** For duplicate categories, strip only `drop_xrefs`, or rewrite duplicate references to `keep_xref`. Consider excluding large/content-like duplicates from default removal unless they also match a noise heuristic.

---

### H2. `SIGNATURE` category misclassifies legitimate images repeated 2–4 times
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** Kimi

The `SIGNATURE` heuristic flags any same image appearing on ≥2 pages but below `min_freq`. The code explicitly ignores position and size. Real tender PDFs can legitimately repeat content images on a few pages: diagrams, stamps that are meaningful, repeated annex graphics, table continuation markers, etc.

**Impact:** `--strip` can remove real content.

**Fix:** Require additional evidence before destructive removal: consistent marginal/header/footer position, small size, alpha/transparency, or explicit opt-in. Rename to `LOW_FREQ_REPEAT` if it remains broad.

---

### H3. Redaction-based drawing/text removal can delete unrelated content inside the rectangle
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5, Kimi, GLM

`page.add_redact_annot(rect, fill=None)` + `apply_redactions(images=PDF_REDACT_IMAGE_NONE)` is spatial redaction. It removes all overlapping non-image content, not only the intended text/drawing operator. This affects both text watermark removal and recurring drawing removal.

**Impact:** headers, separator lines, body text, tables, or vector content intersecting a redaction bbox may be deleted.

**Fix:** Before redacting, check overlap with text spans and skip risky rectangles. For drawings, prefer true content-stream surgery for the specific vector operators or make removal zone/size constrained. Document clearly that PyMuPDF redaction is spatial, not operator-targeted.

---

### H4. Raw content-stream regex for `/Name Do` is brittle
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5, Kimi, GLM

Image stripping removes `/Name Do` with regex over page content streams. PDF streams can contain Form XObjects, nested resources, names with escaped bytes/punctuation, and operator contexts that regex does not parse safely.

**Impact:** valid images may be missed, dangling graphics-state operators may remain, or the wrong XObject can be removed if classification/mapping is wrong.

**Fix:** Use `page.get_images(full=True)` resource names directly where possible. If raw streams must be edited, tokenize PDF content streams instead of regex substitution and recurse into Form XObjects explicitly.

---

### H5. Resource dictionary regex misses nested XObject dictionaries
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5, DeepSeek

`/XObject\s*<<([^>]*)>>` stops at the first `>>`. If the resource dictionary contains nested dictionaries, images after the nested object become invisible to the stripper.

**Impact:** audit detects images, but strip silently fails to remove them.

**Fix:** Replace with a balanced `<< >>` parser or avoid parsing raw resource dictionaries by relying on PyMuPDF image metadata.

---

### H6. AcroForm `/Fields` regex can delete non-signature fields or corrupt the PDF
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** Kimi, DeepSeek

`_strip_digital_signatures()` replaces the entire `/Fields [...]` array with `/Fields[]` via regex. This removes all form fields, not only digital signatures. With nested arrays, the regex can also leave orphaned brackets and produce invalid PDF syntax.

**Impact:** non-signature form fields are destroyed; some PDFs may become malformed.

**Fix:** Traverse the AcroForm field references structurally and remove only signature widget xrefs. Avoid regex replacement of PDF object dictionaries.

---

### H7. Text normalization can merge legitimate numbered headings into watermark patterns
**Severity:** High  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** DeepSeek

`_normalize_for_clustering()` replaces all 1–4 digit numbers with `N`. Headings like `Artículo 1`, `Artículo 2`, `Artículo 3` can collapse into one repeated pattern and be classified as `TEXT_REPEAT`.

**Impact:** legitimate section/article headers may be redacted in bulk.

**Fix:** Exclude common numbered-heading patterns (`Artículo`, `Sección`, `Capítulo`, etc.) from watermark clustering, or use a more conservative similarity strategy that preserves semantic numbering.

---

### M1. `--header-zone` / `--footer-zone` CLI options are inconsistent or ineffective
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5, DeepSeek

The CLI exposes header/footer zone options, but they are not consistently passed through. Detection may use one threshold while stripping/extraction hard-code `0.12/0.88`.

**Impact:** user-provided thresholds can produce false positives/negatives or no visible behavior change.

**Fix:** Thread `header_zone` / `footer_zone` through detection, strip, and extract paths. Store thresholds in the report metadata so all consumers use the same zones.

---

### M2. PAGE_NUMBER removal applies too broadly
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GPT-5.5

Once a page-number candidate exists, stripping/extraction removes any line matching the page-number regex anywhere, not only in detected header/footer zones.

**Impact:** body text such as `Page 5` / `Página 5` can be removed.

**Fix:** Store and enforce detected zone/pattern metadata. Require page-number matches to be in header/footer/margin zones before removal.

---

### M3. Perceptual duplicate detection is quadratic and can merge groups incorrectly
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** Kimi, GLM

The perceptual dedupe loop compares phash groups pairwise and recomputes hash objects inside the inner loop. It also risks overlapping merges without a clear union/disjoint-set model.

**Impact:** slow on image-heavy PDFs; possible incorrect `dedup_map` decisions when several groups are near each other.

**Fix:** Precompute phash objects and use a union-find/disjoint-set structure for transitive grouping. Add a cap or skip for large/content-like images.

---

### M4. Image scanning can spike memory on image-heavy PDFs
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** DeepSeek, GLM

The scan path creates PyMuPDF `Pixmap`, PNG bytes, and PIL images for perceptual hashing. Large or many embedded images can produce significant native/Python memory pressure.

**Impact:** OOM or poor performance on scanned tenders / large annex PDFs.

**Fix:** Release pixmaps promptly via `del pix` / `pix = None` in `finally`, avoid PNG round-trip where possible, batch processing, and consider disabling phash for huge images by default.

---

### M5. `xref_pages` can count the same page multiple times
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** GLM

If the same image xref appears multiple times on the same page, the page is appended multiple times. Frequency calculations then use duplicated page entries.

**Impact:** false HIGH_FREQ/SIGNATURE classifications.

**Fix:** Store pages as `set[int]` during analysis and convert to sorted lists for reports.

---

### M6. PDF reference parsing assumes generation `0`
**Severity:** Medium  
**File:** `scripts/pdf_image_audit.py`  
**Flagged by:** DeepSeek

Regexes assume references like `N 0 R`. PDFs can legally use non-zero generation numbers (`N 1 R`).

**Impact:** resources/fields may be skipped silently on incrementally updated PDFs.

**Fix:** Match `N \d+ R` or use PyMuPDF structured APIs instead of regex.

---

## Recommended Fix Order

1. **H1** — avoid deleting duplicate keepers.
2. **H2** — make `SIGNATURE` / low-frequency repeat removal opt-in or position-constrained.
3. **H3** — add overlap guards before any redaction-based removal.
4. **H6** — stop regex-clearing AcroForm `/Fields`.
5. **H4/H5** — replace PDF dictionary/content regex parsing with structured/tokenized handling.
6. **H7/M2** — tighten text/page-number matching to avoid bulk text loss.
7. **M1** — make CLI zone options actually drive all paths.
8. **M3/M4/M5/M6** — performance and robustness cleanup.

## Suggested Safe Defaults

Until fixes land:

- Make `--strip` refuse broad categories by default:
  - exclude `SIGNATURE`, `DUPLICATE`, `PERCEPTUAL_DUP`, recurring drawings, digital signatures unless explicitly requested.
- Add a `--dry-run` / report-only default (already effectively audit mode).
- Print a strong warning before destructive strip operations.
- Prefer `--categories HIGH_FREQ,TINY,TEXT_REPEAT` only after manual review of the JSON report.
