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


def audit_pdf(input_pdf: Path, output_dir: Path, stem: str, area_ratio: float, min_width_pt: float, min_height_pt: float, render_dpi: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    render_dir = output_dir / f"{stem}_candidate_pages"
    render_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_pdf)
    rows = page_records(doc)
    dom = dominant_size(rows)
    median_area = statistics.median(r["area_pt2"] for r in rows)

    candidates = []
    for r in rows:
        ratio = r["area_pt2"] / median_area if median_area else 1
        reasons = []
        if ratio >= area_ratio:
            reasons.append(f"area_ratio>={area_ratio:g}")
        if r["width_pt"] >= min_width_pt:
            reasons.append(f"width_pt>={min_width_pt:g}")
        if r["height_pt"] >= min_height_pt:
            reasons.append(f"height_pt>={min_height_pt:g}")
        if reasons:
            item = dict(r)
            item["area_ratio_vs_median"] = round(ratio, 3)
            item["candidate_reasons"] = reasons
            item["rendered_image"] = str(render_dir / f"page_{r['page']:04d}.png")
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
        "Información potencialmente útil para procurement:",
    ]
    useful = page_analysis.get("procurement_relevant_info") or []
    if useful:
        for item in useful:
            lines.append(f"- {item}")
    else:
        lines.append("- No se identificó información explícita útil para procurement.")
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


def build_outputs(input_pdf: Path, output_dir: Path, preocr_dir: Path, stem: str, analysis_json: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    preocr_dir.mkdir(parents=True, exist_ok=True)
    analysis = load_analysis(analysis_json)
    doc = fitz.open(input_pdf)

    confirmed = {int(p["page"]): p for p in analysis["pages"] if p.get("is_plan_or_diagram") and p.get("exclude_from_ocr")}
    extracted_pdf = output_dir / f"planos_extraidos_{stem}.pdf"
    preocr_pdf = preocr_dir / f"{stem}_preocr.pdf"
    md_path = output_dir / f"planos_extraidos_{stem}.md"

    extracted = fitz.open()
    for page_num in sorted(confirmed):
        extracted.insert_pdf(doc, from_page=page_num - 1, to_page=page_num - 1)
    if extracted.page_count:
        extracted.save(extracted_pdf)
    else:
        # create an empty marker PDF with one page rather than failing callers expecting path
        p = extracted.new_page(width=595.3, height=841.9)
        p.insert_text((54, 54), "No se confirmaron planos/diagramas para extraer.", fontsize=11)
        extracted.save(extracted_pdf)

    # Dominant page size for replacement pages.
    dom = dominant_size(page_records(doc))
    repl_w, repl_h = float(dom["width_pt"]), float(dom["height_pt"])
    pre = fitz.open()
    extracted_pdf_name = extracted_pdf.name
    for idx in range(1, doc.page_count + 1):
        if idx in confirmed:
            page = pre.new_page(width=repl_w, height=repl_h)
            insert_wrapped_text(page, page_summary_text(confirmed[idx], idx, extracted_pdf_name))
        else:
            pre.insert_pdf(doc, from_page=idx - 1, to_page=idx - 1)
    pre.save(preocr_pdf)

    md_lines = [f"# Planos/diagramas extraídos — {stem}", ""]
    md_lines.append(f"PDF fuente: `{input_pdf}`")
    md_lines.append(f"PDF extraído: `{extracted_pdf}`")
    md_lines.append(f"PDF pre-OCR: `{preocr_pdf}`")
    md_lines.append("")
    for page_num in sorted(confirmed):
        p = confirmed[page_num]
        md_lines.append(f"## Página {page_num} — {p.get('identifier_or_title') or 'Sin identificador/título visible'}")
        md_lines.append("")
        md_lines.append(f"- Tipo: {p.get('visual_type', '')}")
        md_lines.append(f"- Confianza: {p.get('confidence', '')}")
        md_lines.append("")
        md_lines.append(p.get("summary", ""))
        md_lines.append("")
        infos = p.get("procurement_relevant_info") or []
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

    p_build = sub.add_parser("build", parents=[common])
    p_build.add_argument("--preocr-dir", type=Path, required=True)
    p_build.add_argument("--analysis-json", type=Path, required=True)

    args = parser.parse_args()
    stem = args.stem or slugify_stem(args.input_pdf)
    if args.cmd == "audit":
        audit = audit_pdf(args.input_pdf, args.output_dir, stem, args.area_ratio, args.min_width_pt, args.min_height_pt, args.render_dpi)
        print(json.dumps({"audit_json": str(args.output_dir / f"{stem}_page_size_audit.json"), "candidate_count": len(audit["candidates"]), "candidate_ranges": audit["candidate_ranges"]}, indent=2, ensure_ascii=False))
    elif args.cmd == "build":
        print(json.dumps(build_outputs(args.input_pdf, args.output_dir, args.preocr_dir, stem, args.analysis_json), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
