#!/usr/bin/env python3
"""Detect, render, extract, and substitute large plan/diagram pages in PDFs.

Step 1.2b helper for tender_procurement.

Typical flow:
  1) audit/render candidates by page size:
     python scripts/pdf_plan_pages.py audit input_clean.pdf --output-dir artifacts/step_1_planos --stem DOC

  2) analyze rendered PNGs with a visual LLM and write an analysis JSON matching
     instrucciones/schemas/plan_pages_analysis.schema.json

  3) build extracted-plans PDF and pre-OCR substituted PDF:
     python scripts/pdf_plan_pages.py build input_clean.pdf --output-dir artifacts/step_1_planos \
       --preocr-dir artifacts/step_1_pdfs_preocr --stem DOC --analysis-json .../planos_extraidos_DOC.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

POINTS_PER_INCH = 72


def slugify_stem(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"_clean$", "", stem)
    return stem


def page_records(doc: fitz.Document) -> list[dict[str, Any]]:
    rows = []
    for idx, page in enumerate(doc, start=1):
        r = page.rect
        rows.append(
            {
                "page": idx,
                "width_pt": round(float(r.width), 2),
                "height_pt": round(float(r.height), 2),
                "area_pt2": round(float(r.width * r.height), 2),
                "orientation": "landscape" if r.width > r.height else "portrait",
            }
        )
    return rows


def dominant_size(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rounded = [(round(r["width_pt"], 1), round(r["height_pt"], 1)) for r in rows]
    (w, h), count = Counter(rounded).most_common(1)[0]
    return {
        "width_pt": w,
        "height_pt": h,
        "count": count,
        "area_pt2": round(w * h, 2),
        "orientation": "landscape" if w > h else "portrait",
    }


def contiguous_ranges(pages: list[int]) -> list[dict[str, int]]:
    if not pages:
        return []
    pages = sorted(pages)
    ranges = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append({"start": start, "end": prev})
        start = prev = p
    ranges.append({"start": start, "end": prev})
    return ranges


def audit_pdf(input_pdf: Path, output_dir: Path, stem: str, area_ratio: float, min_width_pt: float, min_height_pt: float, render_dpi: int, page_analysis_json: Path | None = None, image_area_ratio_threshold: float = 0.4, image_min_width_pct: float = 0.15, max_images_per_candidate_page: int = 4, max_consecutive_image_heavy: int = 5, image_heavy_doc_pct_disable: float = 0.7) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    render_dir = output_dir / f"{stem}_candidate_pages"
    render_dir.mkdir(parents=True, exist_ok=True)

    # Load existing page analysis if available (from pdf_image_audit.py --page-analysis)
    existing_pa = None
    if page_analysis_json and page_analysis_json.exists():
        existing_pa = json.loads(page_analysis_json.read_text())
    elif not page_analysis_json:
        # Auto-detect: look for {stem}_clean_page_analysis.json next to the input PDF
        auto = input_pdf.parent / f"{input_pdf.stem}_page_analysis.json"
        if auto.exists():
            existing_pa = json.loads(auto.read_text())

    doc = fitz.open(input_pdf)
    rows = page_records(doc)
    dom = dominant_size(rows)
    median_area = statistics.median(r["area_pt2"] for r in rows)

    candidates = []
    pa_pages = {}
    if existing_pa:
        pa_pages = {p["page"]: p for p in existing_pa.get("pages", [])}

    # Anti-scan filter: if too many consecutive pages are image_heavy, it's a scanned doc.
    # Also: if >70% of all pages are image_heavy, disable image_heavy detection entirely.
    image_heavy_pages = set()
    for r in rows:
        pa = pa_pages.get(r["page"])
        # Treat as image-heavy for scan filtering if it crosses the configured area threshold,
        # not only if pdf_image_audit emitted the image_heavy signal.
        if pa and pa.get("image_area_ratio", 0) >= image_area_ratio_threshold:
            image_heavy_pages.add(r["page"])

    # Check if document is predominantly scanned
    total_pages = len(rows)
    image_heavy_doc_pct = len(image_heavy_pages) / total_pages if total_pages else 0
    disable_image_heavy = image_heavy_doc_pct > image_heavy_doc_pct_disable

    # Find runs of consecutive image_heavy pages > threshold
    max_consecutive = max_consecutive_image_heavy
    consecutive_image_heavy_runs = set()
    if image_heavy_pages and not disable_image_heavy:
        sorted_ih = sorted(image_heavy_pages)
        run_start = sorted_ih[0]
        run_prev = sorted_ih[0]
        for p in sorted_ih[1:]:
            if p == run_prev + 1:
                run_prev = p
            else:
                if (run_prev - run_start + 1) > max_consecutive:
                    for pp in range(run_start, run_prev + 1):
                        consecutive_image_heavy_runs.add(pp)
                run_start = run_prev = p
        if (run_prev - run_start + 1) > max_consecutive:
            for pp in range(run_start, run_prev + 1):
                consecutive_image_heavy_runs.add(pp)

    for r in rows:
        ratio = r["area_pt2"] / median_area if median_area else 1
        reasons = []

        # Size-based detection (original)
        if ratio >= area_ratio:
            reasons.append(f"area_ratio>={area_ratio:g}")
        if r["width_pt"] >= min_width_pt:
            reasons.append(f"width_pt>={min_width_pt:g}")
        if r["height_pt"] >= min_height_pt:
            reasons.append(f"height_pt>={min_height_pt:g}")

        # Content-based detection from page analysis
        pa = pa_pages.get(r["page"])
        if pa:
            signals = pa.get("plan_candidate_signals", [])
            # Strong signals that indicate plan/diagram regardless of page size
            if "high_drawing_count" in signals and pa.get("drawing_count", 0) > 5000:
                if "high_drawing_count" not in str(reasons):
                    reasons.append(f"drawing_count={pa['drawing_count']}")
            if "high_drawing_ratio" in signals and pa.get("drawing_area_ratio", 0) > 0.3:
                if "high_drawing_ratio" not in str(reasons):
                    reasons.append(f"drawing_area_ratio={pa['drawing_area_ratio']}")
            if "autocad_like" in signals:
                if "autocad_like" not in str(reasons):
                    reasons.append("autocad_like")
            # Image_heavy detection with anti-scan and width filters
            if pa.get("image_area_ratio", 0) >= image_area_ratio_threshold:
                # Skip if document is predominantly scanned
                if disable_image_heavy:
                    pass  # skip image_heavy entirely for scanned docs
                # Skip if in a long consecutive run
                elif r["page"] in consecutive_image_heavy_runs:
                    pass  # skip, likely scanned pages
                # Skip if too many images (probably scanned page with segments)
                elif pa.get("image_count", 0) > max_images_per_candidate_page:
                    pass  # skip, too many images
                else:
                    # Width filter: each image should be reasonably wide (not marginal)
                    # We check if the largest image block covers >15% of page width
                    img_blocks = []
                    try:
                        p_doc = doc[r["page"] - 1]
                        blocks_data = p_doc.get_text("dict")["blocks"]
                        for b in blocks_data:
                            if b["type"] == 1:
                                bbox = b.get("bbox", (0, 0, 0, 0))
                                img_w = bbox[2] - bbox[0]
                                page_w = r["width_pt"]
                                if page_w > 0 and img_w / page_w >= image_min_width_pct:
                                    img_blocks.append(bbox)
                    except Exception:
                        img_blocks = []
                    if img_blocks:
                        if "image_heavy" not in str(reasons):
                            reasons.append(f"image_area_ratio={pa['image_area_ratio']}")
                    # If no image passes width filter, skip
                    # (marginal decoration not caught by cleaner)

        if reasons:
            item = dict(r)
            item["area_ratio_vs_median"] = round(ratio, 3)
            item["candidate_reasons"] = reasons
            item["rendered_image"] = str(render_dir / f"page_{r['page']:04d}.png")
            # Include content metrics from page analysis if available
            if pa:
                for key in ["text_char_count", "text_block_count", "image_count", "image_block_count",
                            "image_area_ratio", "drawing_count", "drawing_area_ratio",
                            "text_density_chars_per_pt2", "content_dominant", "plan_candidate_signals"]:
                    if key in pa:
                        item[f"pa_{key}"] = pa[key]
            candidates.append(item)

    matrix = fitz.Matrix(render_dpi / POINTS_PER_INCH, render_dpi / POINTS_PER_INCH)
    for c in candidates:
        page = doc[c["page"] - 1]
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(c["rendered_image"])

    audit = {
        "input_pdf": str(input_pdf),
        "page_count": len(rows),
        "dominant_page_size": dom,
        "median_area_pt2": round(median_area, 2),
        "candidate_detection": {
            "area_ratio_threshold": area_ratio,
            "min_width_pt": min_width_pt,
            "min_height_pt": min_height_pt,
            "render_dpi": render_dpi,
            "image_area_ratio_threshold": image_area_ratio_threshold,
            "image_min_width_pct": image_min_width_pct,
            "max_images_per_candidate_page": max_images_per_candidate_page,
            "max_consecutive_image_heavy": max_consecutive_image_heavy,
            "image_heavy_doc_pct_disable": image_heavy_doc_pct_disable,
            "image_heavy_doc_pct": round(image_heavy_doc_pct, 3),
            "disable_image_heavy": disable_image_heavy,
        },
        "candidate_ranges": contiguous_ranges([c["page"] for c in candidates]),
        "pages": rows,
        "candidates": candidates,
    }
    out = output_dir / f"{stem}_page_size_audit.json"
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    return audit


def load_analysis(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if "pages" not in data or not isinstance(data["pages"], list):
        raise SystemExit("analysis JSON must contain pages[]")
    return data


def page_summary_text(page_analysis: dict[str, Any], source_page: int, extracted_pdf_name: str) -> str:
    title = page_analysis.get("identifier_or_title") or f"Plano/diagrama página {source_page}"
    lines = [
        f"Página {source_page} reemplazada: plano/diagrama detectado",
        "",
        f"Identificador/título: {title}",
        f"Tipo visual: {page_analysis.get('visual_type', 'no especificado')}",
        f"Archivo extraído: {extracted_pdf_name}",
        "",
        "Resumen visual:",
        page_analysis.get("summary", ""),
        "",
        "Información técnica visible:",
    ]
    useful = page_analysis.get("technical_observations") or []
    if useful:
        for item in useful:
            lines.append(f"- {item}")
    else:
        lines.append("- No se identificó información técnica explícita visible.")
    lines += ["", "Texto/códigos visibles:"]
    visible = page_analysis.get("visible_text_or_codes") or []
    if visible:
        for item in visible:
            lines.append(f"- {item}")
    else:
        lines.append("- No legible / no detectado.")
    lines += ["", "Limitaciones:"]
    limitations = page_analysis.get("limitations") or []
    if limitations:
        for item in limitations:
            lines.append(f"- {item}")
    else:
        lines.append("- Análisis visual resumido; no usar para inferir cantidades no explícitas.")
    return "\n".join(lines).strip() + "\n"


def insert_wrapped_text(page: fitz.Page, text: str, margin: float = 54) -> None:
    rect = page.rect
    y = margin
    # Title first line larger
    chunks = text.split("\n")
    font_size = 10.5
    line_h = font_size * 1.35
    max_chars = max(60, int((rect.width - 2 * margin) / (font_size * 0.48)))
    for idx, raw in enumerate(chunks):
        if raw == "":
            y += line_h * 0.6
            continue
        wrapped = textwrap.wrap(raw, width=max_chars, replace_whitespace=False) or [raw]
        for line in wrapped:
            if y > rect.height - margin:
                page.insert_text((margin, y), "[continúa en metadata JSON]", fontsize=font_size, fontname="helv")
                return
            if idx == 0:
                page.insert_text((margin, y), line, fontsize=14, fontname="helv", color=(0, 0, 0))
            else:
                page.insert_text((margin, y), line, fontsize=font_size, fontname="helv", color=(0, 0, 0))
            y += line_h if idx else 18


def add_red_page_label(page: fitz.Page, source_page: int) -> None:
    """Stamp a red source-page label in the upper-right corner."""
    label = f"pag. {source_page}"
    w = page.rect.width
    rect = fitz.Rect(w - 120, 12, w - 12, 48)
    try:
        page.wrap_contents()
    except Exception:
        pass
    page.draw_rect(rect, color=(1, 0, 0), fill=(1, 1, 1), width=2)
    # insert_text is more reliable than insert_textbox for very large/rotated CAD pages.
    page.insert_text(fitz.Point(rect.x0 + 10, rect.y0 + 22), label, fontsize=13, fontname="helv", color=(1, 0, 0))


def image_block_rects(page: fitz.Page) -> list[fitz.Rect]:
    rects = []
    try:
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") == 1:
                rects.append(fitz.Rect(b["bbox"]))
    except Exception:
        pass
    return rects


def resolve_rendered_image_rect(page: fitz.Page, candidate: fitz.Rect) -> fitz.Rect:
    """Resolve an approximate visual bbox to the actual rendered image rect.

    The analysis bbox is only a locator. For redaction we must use the final
    rendered image position from the PDF page, otherwise we can erase surrounding
    text. PyMuPDF image blocks already include page transforms / masks in page
    coordinates, so we select the intersecting rendered image blocks and return
    their union. If no image block matches, fall back to the candidate without
    adding any margin.
    """
    selected: list[fitz.Rect] = []
    for img_rect in image_block_rects(page):
        inter = candidate & img_rect
        if inter.is_empty:
            continue
        img_area = max(1.0, img_rect.width * img_rect.height)
        cand_area = max(1.0, candidate.width * candidate.height)
        # Strong overlap with the rendered image, or image center inside the
        # candidate. The center rule catches small tiles/fragments wholly inside
        # a larger region; the overlap rule handles near-exact bboxes.
        center = fitz.Point((img_rect.x0 + img_rect.x1) / 2, (img_rect.y0 + img_rect.y1) / 2)
        if (inter.get_area() / img_area) >= 0.35 or (inter.get_area() / cand_area) >= 0.35 or candidate.contains(center):
            selected.append(img_rect)

    if not selected:
        resolved = fitz.Rect(candidate)
    else:
        resolved = fitz.Rect(selected[0])
        for img_rect in selected[1:]:
            resolved |= img_rect

    resolved.x0 = max(0, resolved.x0)
    resolved.y0 = max(0, resolved.y0)
    resolved.x1 = min(page.rect.width, resolved.x1)
    resolved.y1 = min(page.rect.height, resolved.y1)
    return resolved




def make_replacement_image_stream(width_pt: float, height_pt: float, text: str, scale: float = 2.0) -> bytes:
    """Render replacement text into a PNG image sized to the target PDF rect.

    The image is inserted back into the PDF so downstream OCR sees a normal image,
    not PDF overlay text. Background is 15% gray (0.85 RGB) to make it obvious
    that an original image/diagram was replaced.
    """
    width_pt = max(72.0, float(width_pt))
    height_pt = max(36.0, float(height_pt))
    tmp = fitz.open()
    page = tmp.new_page(width=width_pt, height=height_pt)
    page.draw_rect(page.rect, color=(0.55, 0.55, 0.55), fill=(0.85, 0.85, 0.85), width=1)

    margin = max(6.0, min(width_pt, height_pt) * 0.035)
    fontsize = max(5.5, min(10.0, height_pt / 18.0))
    line_h = fontsize * 1.28
    max_chars = max(24, int((width_pt - 2 * margin) / (fontsize * 0.48)))
    y = margin + fontsize
    for raw in text.split("\n"):
        wrapped = textwrap.wrap(raw, width=max_chars, replace_whitespace=False) or [raw]
        for line in wrapped:
            if y > height_pt - margin:
                page.insert_text(fitz.Point(margin, y), "[continúa en JSON de análisis]", fontsize=fontsize, fontname="helv", color=(0, 0, 0))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                data = pix.tobytes("png")
                tmp.close()
                return data
            page.insert_text(fitz.Point(margin, y), line, fontsize=fontsize, fontname="helv", color=(0, 0, 0))
            y += line_h
        y += line_h * 0.35
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    data = pix.tobytes("png")
    tmp.close()
    return data

def build_outputs(input_pdf: Path, output_dir: Path, preocr_dir: Path, stem: str, analysis_json: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    preocr_dir.mkdir(parents=True, exist_ok=True)
    analysis = load_analysis(analysis_json)
    doc = fitz.open(input_pdf)

    # Build lookup by page number
    pages_by_num = {int(p["page"]): p for p in analysis["pages"]}
    # replace_page: entire page replaced (old exclude_from_ocr)
    replace_page_set = {n for n, p in pages_by_num.items() if p.get("action") == "replace_page"}
    # replace_images: only regions replaced
    replace_images_set = {n for n, p in pages_by_num.items() if p.get("action") == "replace_images"}

    extracted_pdf = output_dir / f"planos_extraidos_{stem}.pdf"
    preocr_pdf = preocr_dir / f"{stem}_preocr.pdf"
    md_path = output_dir / f"planos_extraidos_{stem}.md"

    # Extract affected pages in original document order, with source-page stamp.
    extracted = fitz.open()
    affected_pages = sorted(replace_page_set | replace_images_set)
    for page_num in affected_pages:
        # Render the original page into a fresh page. This normalizes rotated CAD pages
        # so the corner label is reliably visible/extractable.
        src_page = doc[page_num - 1]
        new_page = extracted.new_page(width=src_page.rect.width, height=src_page.rect.height)
        new_page.show_pdf_page(new_page.rect, doc, page_num - 1)
        add_red_page_label(new_page, page_num)
    if extracted.page_count:
        extracted.save(extracted_pdf)
    else:
        p = extracted.new_page(width=595.3, height=841.9)
        p.insert_text((54, 54), "No se confirmaron planos/diagramas para extraer.", fontsize=11)
        extracted.save(extracted_pdf)

    # Build pre-OCR PDF
    dom = dominant_size(page_records(doc))
    repl_w, repl_h = float(dom["width_pt"]), float(dom["height_pt"])
    pre = fitz.open()
    extracted_pdf_name = extracted_pdf.name

    for idx in range(1, doc.page_count + 1):
        if idx in replace_page_set:
            # Entire page replaced with text summary
            page = pre.new_page(width=repl_w, height=repl_h)
            insert_wrapped_text(page, page_summary_text(pages_by_num[idx], idx, extracted_pdf_name))
        elif idx in replace_images_set:
            # Copy original page, then white-out replaced regions and insert text
            pre.insert_pdf(doc, from_page=idx - 1, to_page=idx - 1)
            pre_page = pre[pre.page_count - 1]
            pa = pages_by_num[idx]
            replacement_jobs = []
            for repl in pa.get("image_replacements", []):
                bbox_pct = repl.get("bbox_pct", [0, 0, 1, 1])
                # Convert pct locator to page coordinates without margin. Then resolve
                # to the actual rendered image rect before redaction/replacement.
                x0 = max(0, bbox_pct[0]) * pre_page.rect.width
                y0 = max(0, bbox_pct[1]) * pre_page.rect.height
                x1 = min(1, bbox_pct[2]) * pre_page.rect.width
                y1 = min(1, bbox_pct[3]) * pre_page.rect.height
                candidate_rect = fitz.Rect(x0, y0, x1, y1)
                rect = resolve_rendered_image_rect(pre_page, candidate_rect)

                desc_lines = ["[Diagrama/imagen reemplazado — ver planos_extraidos]"]
                if repl.get("description"):
                    desc_lines.append(repl["description"])
                codes = repl.get("visible_text_or_codes", [])
                if codes:
                    desc_lines.append("Códigos/texto visible: " + ", ".join(str(c) for c in codes[:10]))
                infos = repl.get("technical_observations", [])
                if infos:
                    desc_lines.append("Info técnica visible: " + "; ".join(str(i) for i in infos[:5]))
                text_to_render = "\n".join(desc_lines)
                png_stream = make_replacement_image_stream(rect.width, rect.height, text_to_render)
                replacement_jobs.append((rect, png_stream))

            # Redact all target rects first, then insert replacement images.
            # This removes original image tiles/strips cleanly while avoiding PDF text overlays.
            for rect, _png_stream in replacement_jobs:
                pre_page.add_redact_annot(rect, fill=(1, 1, 1))
            if replacement_jobs:
                pre_page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_REMOVE,
                    graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=fitz.PDF_REDACT_TEXT_NONE,
                )
            for rect, png_stream in replacement_jobs:
                pre_page.insert_image(rect, stream=png_stream, keep_proportion=False)
        else:
            # leave_for_ocr or not in analysis: copy as-is
            pre.insert_pdf(doc, from_page=idx - 1, to_page=idx - 1)
    pre.save(preocr_pdf)

    # Build MD report
    md_lines = [f"# Planos/diagramas extraídos — {stem}", ""]
    md_lines.append(f"PDF fuente: `{input_pdf}`")
    md_lines.append(f"PDF extraído: `{extracted_pdf}`")
    md_lines.append(f"PDF pre-OCR: `{preocr_pdf}`")
    md_lines.append("")
    for page_num in sorted(pages_by_num.keys()):
        p = pages_by_num[page_num]
        action = p.get("action", "leave_for_ocr")
        if action == "leave_for_ocr":
            continue
        title = p.get('identifier_or_title') or 'Sin identificador/título visible'
        md_lines.append(f"## Página {page_num} — {title}")
        md_lines.append("")
        md_lines.append(f"- Acción: {action}")
        md_lines.append(f"- Tipo: {p.get('visual_type', '')}")
        md_lines.append(f"- Confianza: {p.get('confidence', '')}")
        md_lines.append("")
        md_lines.append(p.get("summary", ""))
        md_lines.append("")
        if action == "replace_images" and p.get("image_replacements"):
            md_lines.append("### Regiones reemplazadas")
            for repl in p["image_replacements"]:
                md_lines.append(f"\n**Región {repl.get('region_id', '?')}** (bbox: {repl.get('bbox_pct')})")
                md_lines.append(f"> {repl.get('description', '')}")
                codes = repl.get("visible_text_or_codes", [])
                if codes:
                    md_lines.append(f"Códigos: {', '.join(str(c) for c in codes)}")
                infos = repl.get("technical_observations", [])
                if infos:
                    md_lines.append(f"Info técnica visible: {'; '.join(str(i) for i in infos)}")
                md_lines.append("")
        infos = p.get("technical_observations") or []
        if infos:
            md_lines.append("Información útil:")
            md_lines.extend(f"- {x}" for x in infos)
            md_lines.append("")
        codes = p.get("visible_text_or_codes") or []
        if codes:
            md_lines.append("Texto/códigos visibles:")
            md_lines.extend(f"- {x}" for x in codes)
            md_lines.append("")
    md_path.write_text("\n".join(md_lines).rstrip() + "\n")

    return {"extracted_pdf": str(extracted_pdf), "preocr_pdf": str(preocr_pdf), "md": str(md_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input_pdf", type=Path)
    common.add_argument("--output-dir", type=Path, required=True)
    common.add_argument("--stem", default=None)

    p_audit = sub.add_parser("audit", parents=[common])
    p_audit.add_argument("--area-ratio", type=float, default=1.4)
    p_audit.add_argument("--min-width-pt", type=float, default=700)
    p_audit.add_argument("--min-height-pt", type=float, default=1000)
    p_audit.add_argument("--render-dpi", type=int, default=180)
    p_audit.add_argument("--page-analysis", type=Path, default=None,
                        help="Path to {stem}_page_analysis.json from pdf_image_audit.py (auto-detected if not given)")
    p_audit.add_argument("--image-area-ratio-threshold", type=float, default=0.4,
                        help="Minimum image area ratio to consider replace_images visual analysis (default 0.4; try 0.2 for broader detection)")
    p_audit.add_argument("--image-min-width-pct", type=float, default=0.15,
                        help="Minimum width fraction for an image block to avoid marginal decorations (default 0.15)")
    p_audit.add_argument("--max-images-per-candidate-page", type=int, default=4,
                        help="Skip image-heavy page if it has more images than this (default 4)")
    p_audit.add_argument("--max-consecutive-image-heavy", type=int, default=5,
                        help="Skip image-heavy runs longer than this, likely scanned docs (default 5)")
    p_audit.add_argument("--image-heavy-doc-pct-disable", type=float, default=0.7,
                        help="Disable image-heavy detection if doc ratio exceeds this (default 0.7)")

    p_build = sub.add_parser("build", parents=[common])
    p_build.add_argument("--preocr-dir", type=Path, required=True)
    p_build.add_argument("--analysis-json", type=Path, required=True)

    args = parser.parse_args()
    stem = args.stem or slugify_stem(args.input_pdf)
    if args.cmd == "audit":
        audit = audit_pdf(
            args.input_pdf, args.output_dir, stem,
            args.area_ratio, args.min_width_pt, args.min_height_pt, args.render_dpi,
            getattr(args, 'page_analysis', None),
            args.image_area_ratio_threshold,
            args.image_min_width_pct,
            args.max_images_per_candidate_page,
            args.max_consecutive_image_heavy,
            args.image_heavy_doc_pct_disable,
        )
        print(json.dumps({"audit_json": str(args.output_dir / f"{stem}_page_size_audit.json"), "candidate_count": len(audit["candidates"]), "candidate_ranges": audit["candidate_ranges"]}, indent=2, ensure_ascii=False))
    elif args.cmd == "build":
        print(json.dumps(build_outputs(args.input_pdf, args.output_dir, args.preocr_dir, stem, args.analysis_json), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
