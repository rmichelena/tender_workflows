#!/usr/bin/env python3
"""
pdf_visual_diff.py — Visual QA companion for PDF cleaning/manipulation pipelines.

Compares two PDFs by rendering pages to images, computing visual diffs, and
producing a static HTML viewer that can also be served over HTTP.

Typical usage:
  python pdf_visual_diff.py compare original.pdf cleaned.pdf --out diff_run --dpi 160
  python pdf_visual_diff.py serve diff_run --host 0.0.0.0 --port 8787

Dependencies:
  pip install pymupdf pillow flask

Optional dependencies:
  pip install opencv-python-headless numpy

If OpenCV/numpy are not installed, the script falls back to PIL-only diffing.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import dataclasses
import hashlib
import html
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import fitz  # PyMuPDF
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pymupdf. Install with: pip install pymupdf") from exc

try:
    from PIL import Image, ImageChops, ImageDraw, ImageOps
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pillow. Install with: pip install pillow") from exc

try:
    import numpy as np
    import cv2
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False
    np = None  # type: ignore
    cv2 = None  # type: ignore


@dataclasses.dataclass
class PageResult:
    page: int
    status: str
    changed_pixels: int
    total_pixels: int
    changed_pixels_pct: float
    changed_regions: int
    largest_region_pct: float
    risk: str
    original_image: str | None
    processed_image: str | None
    diff_image: str | None
    overlay_image: str | None
    regions: list[dict[str, Any]]
    notes: list[str]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def render_page(pdf_path: Path, page_index: int, dpi: int, output: Path) -> dict[str, Any]:
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(output)
        return {
            "width_pt": float(page.rect.width),
            "height_pt": float(page.rect.height),
            "rotation": int(page.rotation),
            "render_width_px": int(pix.width),
            "render_height_px": int(pix.height),
        }


def pad_to_same_size(a: Image.Image, b: Image.Image) -> tuple[Image.Image, Image.Image, list[str]]:
    notes: list[str] = []
    if a.size == b.size:
        return a.convert("RGB"), b.convert("RGB"), notes
    w = max(a.width, b.width)
    h = max(a.height, b.height)
    notes.append(f"render_sizes_differ: original={a.width}x{a.height}, processed={b.width}x{b.height}; padded_to={w}x{h}")
    aa = Image.new("RGB", (w, h), "white")
    bb = Image.new("RGB", (w, h), "white")
    aa.paste(a.convert("RGB"), (0, 0))
    bb.paste(b.convert("RGB"), (0, 0))
    return aa, bb, notes


def classify_risk(changed_pct: float, largest_region_pct: float, changed_regions: int) -> str:
    if changed_pct >= 0.20 or largest_region_pct >= 0.12:
        return "high"
    if changed_pct >= 0.05 or largest_region_pct >= 0.03 or changed_regions >= 20:
        return "medium"
    if changed_pct >= 0.005 or changed_regions > 0:
        return "low"
    return "none"


def compute_diff_pil(orig_path: Path, proc_path: Path, diff_path: Path, overlay_path: Path,
                     threshold: int, min_region_area_px: int) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    orig = Image.open(orig_path)
    proc = Image.open(proc_path)
    orig, proc, size_notes = pad_to_same_size(orig, proc)
    notes.extend(size_notes)

    diff = ImageChops.difference(orig, proc)
    gray = ImageOps.grayscale(diff)
    mask = gray.point(lambda p: 255 if p >= threshold else 0)

    # PIL fallback has no contour grouping. Treat as one region if any changed pixels.
    hist = mask.histogram()
    changed_pixels = int(hist[255])
    total_pixels = mask.width * mask.height
    regions: list[dict[str, Any]] = []
    if changed_pixels:
        bbox = mask.getbbox()
        if bbox:
            x0, y0, x1, y1 = bbox
            area = (x1 - x0) * (y1 - y0)
            if area >= min_region_area_px:
                regions.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "area_px": area})

    # Create black/white diff with bounding box in grayscale fallback.
    diff_rgb = Image.new("RGB", mask.size, "white")
    diff_rgb.paste(Image.new("RGB", mask.size, "black"), mask=mask)
    draw = ImageDraw.Draw(diff_rgb)
    for r in regions:
        draw.rectangle([r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]], outline="black", width=3)
    diff_rgb.save(diff_path)

    overlay = proc.copy()
    overlay_mark = Image.new("RGB", overlay.size, "white")
    overlay_mark.paste(Image.new("RGB", overlay.size, (255, 0, 0)), mask=mask)
    overlay = Image.blend(overlay, overlay_mark, alpha=0.35)
    draw_o = ImageDraw.Draw(overlay)
    for r in regions:
        draw_o.rectangle([r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]], outline=(255, 0, 0), width=3)
    overlay.save(overlay_path)

    largest = max((r["area_px"] for r in regions), default=0)
    metrics = {
        "changed_pixels": changed_pixels,
        "total_pixels": total_pixels,
        "changed_pixels_pct": changed_pixels / total_pixels if total_pixels else 0,
        "changed_regions": len(regions),
        "largest_region_pct": largest / total_pixels if total_pixels else 0,
        "regions": regions,
    }
    notes.append("opencv_not_available: used PIL fallback; region detection is approximate")
    return metrics, notes


def compute_diff_cv2(orig_path: Path, proc_path: Path, diff_path: Path, overlay_path: Path,
                     threshold: int, min_region_area_px: int) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    orig_pil = Image.open(orig_path)
    proc_pil = Image.open(proc_path)
    orig_pil, proc_pil, size_notes = pad_to_same_size(orig_pil, proc_pil)
    notes.extend(size_notes)

    orig = np.array(orig_pil)  # type: ignore[union-attr]
    proc = np.array(proc_pil)  # type: ignore[union-attr]
    absdiff = cv2.absdiff(orig, proc)  # type: ignore[union-attr]
    gray = cv2.cvtColor(absdiff, cv2.COLOR_RGB2GRAY)  # type: ignore[union-attr]
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)  # type: ignore[union-attr]

    # Morphological close to merge anti-aliased strokes into meaningful regions.
    kernel = np.ones((3, 3), np.uint8)  # type: ignore[union-attr]
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)  # type: ignore[union-attr]
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)  # type: ignore[union-attr]

    regions = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)  # type: ignore[union-attr]
        area = int(w * h)
        if area < min_region_area_px:
            continue
        regions.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "area_px": area})
    regions.sort(key=lambda r: r["area_px"], reverse=True)

    changed_pixels = int(np.count_nonzero(mask))  # type: ignore[union-attr]
    total_pixels = int(mask.shape[0] * mask.shape[1])

    diff_rgb = np.full_like(orig, 255)
    diff_rgb[mask > 0] = [255, 0, 0]
    for r in regions:
        cv2.rectangle(diff_rgb, (r["x"], r["y"]), (r["x"] + r["w"], r["y"] + r["h"]), (0, 0, 0), 3)  # type: ignore[union-attr]
    Image.fromarray(diff_rgb).save(diff_path)

    overlay = proc.copy()
    overlay[mask > 0] = (0.65 * overlay[mask > 0] + 0.35 * np.array([255, 0, 0])).astype(np.uint8)  # type: ignore[union-attr]
    for r in regions:
        cv2.rectangle(overlay, (r["x"], r["y"]), (r["x"] + r["w"], r["y"] + r["h"]), (255, 0, 0), 3)  # type: ignore[union-attr]
    Image.fromarray(overlay).save(overlay_path)

    largest = max((r["area_px"] for r in regions), default=0)
    metrics = {
        "changed_pixels": changed_pixels,
        "total_pixels": total_pixels,
        "changed_pixels_pct": changed_pixels / total_pixels if total_pixels else 0,
        "changed_regions": len(regions),
        "largest_region_pct": largest / total_pixels if total_pixels else 0,
        "regions": regions,
    }
    return metrics, notes


def get_page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return int(doc.page_count)


def render_all(pdf_path: Path, out_dir: Path, prefix: str, page_count: int, dpi: int, workers: int) -> list[dict[str, Any] | None]:
    ensure_dir(out_dir)
    meta: list[dict[str, Any] | None] = [None] * page_count

    def task(i: int) -> tuple[int, dict[str, Any]]:
        out = out_dir / f"{prefix}_page_{i+1:04d}.png"
        m = render_page(pdf_path, i, dpi, out)
        m["image"] = out.name
        return i, m

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(task, i) for i in range(page_count)]
        for fut in concurrent.futures.as_completed(futures):
            i, m = fut.result()
            meta[i] = m
    return meta


def compare_pdfs(original: Path, processed: Path, out_dir: Path, dpi: int, threshold: int,
                 min_region_area_px: int, workers: int, title: str | None) -> dict[str, Any]:
    ensure_dir(out_dir)
    images_dir = out_dir / "images"
    orig_dir = images_dir / "original"
    proc_dir = images_dir / "processed"
    diff_dir = images_dir / "diff"
    overlay_dir = images_dir / "overlay"
    for d in (orig_dir, proc_dir, diff_dir, overlay_dir):
        ensure_dir(d)

    orig_pages = get_page_count(original)
    proc_pages = get_page_count(processed)
    max_pages = max(orig_pages, proc_pages)
    common_pages = min(orig_pages, proc_pages)

    orig_meta = render_all(original, orig_dir, "original", orig_pages, dpi, workers)
    proc_meta = render_all(processed, proc_dir, "processed", proc_pages, dpi, workers)

    pages: list[PageResult] = []
    for i in range(max_pages):
        page_no = i + 1
        notes: list[str] = []
        if i >= orig_pages:
            pages.append(PageResult(page_no, "added_page", 0, 0, 1.0, 1, 1.0, "high", None,
                                    f"images/processed/processed_page_{page_no:04d}.png", None, None, [],
                                    ["page_exists_only_in_processed_pdf"]))
            continue
        if i >= proc_pages:
            pages.append(PageResult(page_no, "removed_page", 0, 0, 1.0, 1, 1.0, "high",
                                    f"images/original/original_page_{page_no:04d}.png", None, None, None, [],
                                    ["page_exists_only_in_original_pdf"]))
            continue

        orig_img = orig_dir / f"original_page_{page_no:04d}.png"
        proc_img = proc_dir / f"processed_page_{page_no:04d}.png"
        diff_img = diff_dir / f"diff_page_{page_no:04d}.png"
        overlay_img = overlay_dir / f"overlay_page_{page_no:04d}.png"
        if HAS_CV2:
            metrics, diff_notes = compute_diff_cv2(orig_img, proc_img, diff_img, overlay_img, threshold, min_region_area_px)
        else:
            metrics, diff_notes = compute_diff_pil(orig_img, proc_img, diff_img, overlay_img, threshold, min_region_area_px)
        notes.extend(diff_notes)
        changed_pct = metrics["changed_pixels_pct"]
        largest_pct = metrics["largest_region_pct"]
        risk = classify_risk(changed_pct, largest_pct, metrics["changed_regions"])
        status = "identical" if risk == "none" else "changed"
        pages.append(PageResult(
            page=page_no,
            status=status,
            changed_pixels=metrics["changed_pixels"],
            total_pixels=metrics["total_pixels"],
            changed_pixels_pct=changed_pct,
            changed_regions=metrics["changed_regions"],
            largest_region_pct=largest_pct,
            risk=risk,
            original_image=f"images/original/original_page_{page_no:04d}.png",
            processed_image=f"images/processed/processed_page_{page_no:04d}.png",
            diff_image=f"images/diff/diff_page_{page_no:04d}.png",
            overlay_image=f"images/overlay/overlay_page_{page_no:04d}.png",
            regions=metrics["regions"][:50],
            notes=notes,
        ))

    summary = {
        "title": title or f"{original.name} vs {processed.name}",
        "created_by": "pdf_visual_diff.py",
        "original_pdf": str(original),
        "processed_pdf": str(processed),
        "original_sha256": sha256_file(original),
        "processed_sha256": sha256_file(processed),
        "original_pages": orig_pages,
        "processed_pages": proc_pages,
        "common_pages": common_pages,
        "dpi": dpi,
        "threshold": threshold,
        "min_region_area_px": min_region_area_px,
        "opencv_enabled": HAS_CV2,
        "page_count_changed": orig_pages != proc_pages,
        "pages_changed": sum(1 for p in pages if p.status == "changed"),
        "pages_identical": sum(1 for p in pages if p.status == "identical"),
        "pages_added": sum(1 for p in pages if p.status == "added_page"),
        "pages_removed": sum(1 for p in pages if p.status == "removed_page"),
        "high_risk_pages": [p.page for p in pages if p.risk == "high"],
        "medium_risk_pages": [p.page for p in pages if p.risk == "medium"],
        "low_risk_pages": [p.page for p in pages if p.risk == "low"],
    }
    data = {
        "summary": summary,
        "pages": [dataclasses.asdict(p) for p in pages],
        "original_page_metadata": orig_meta,
        "processed_page_metadata": proc_meta,
    }
    (out_dir / "summary.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    write_viewer(out_dir, data)
    return data


def risk_badge(risk: str) -> str:
    labels = {"none": "Sin cambios", "low": "Bajo", "medium": "Medio", "high": "Alto"}
    return labels.get(risk, risk)


def write_viewer(out_dir: Path, data: dict[str, Any]) -> None:
    # Data is loaded as external JSON to avoid huge inline HTML.
    title = html.escape(data["summary"].get("title", "PDF Visual Diff"))
    html_text = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ --bg:#111; --panel:#1b1b1b; --muted:#aaa; --text:#f2f2f2; --line:#333; }}
    body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--text); }}
    header {{ position:sticky; top:0; z-index:3; background:#0d0d0d; border-bottom:1px solid var(--line); padding:10px 16px; }}
    h1 {{ font-size:18px; margin:0 0 8px; }}
    .summary {{ display:flex; flex-wrap:wrap; gap:8px; font-size:13px; color:var(--muted); }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:3px 9px; background:var(--panel); }}
    .toolbar {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    button, select, input {{ background:#222; color:var(--text); border:1px solid #444; border-radius:6px; padding:6px 8px; }}
    button:hover {{ background:#303030; cursor:pointer; }}
    main {{ display:grid; grid-template-columns:260px 1fr; min-height:calc(100vh - 88px); }}
    nav {{ border-right:1px solid var(--line); background:#151515; overflow:auto; height:calc(100vh - 88px); position:sticky; top:88px; }}
    .pageBtn {{ display:block; width:100%; text-align:left; border:0; border-bottom:1px solid #262626; border-radius:0; padding:9px 12px; background:transparent; }}
    .pageBtn.active {{ background:#333; }}
    .risk-none {{ color:#8be28b; }} .risk-low {{ color:#e6d36c; }} .risk-medium {{ color:#ffac45; }} .risk-high {{ color:#ff6868; }}
    .viewer {{ padding:12px; overflow:hidden; }}
    .pageMeta {{ font-size:13px; color:var(--muted); margin-bottom:10px; display:flex; flex-wrap:wrap; gap:8px; }}
    .grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; align-items:start; }}
    .pane {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    .pane h2 {{ margin:0; padding:8px 10px; font-size:14px; border-bottom:1px solid var(--line); background:#202020; }}
    .scrollbox {{ height:calc(100vh - 210px); overflow:auto; }}
    img.pageImg {{ transform-origin:top left; display:block; background:white; max-width:none; }}
    .hidden {{ display:none; }}
    .notes {{ font-size:12px; color:#ccc; margin:8px 0; white-space:pre-wrap; }}
    @media (max-width:1000px) {{ main {{ grid-template-columns:1fr; }} nav {{ position:relative; top:0; height:180px; }} .grid {{ grid-template-columns:1fr; }} .scrollbox {{ height:70vh; }} }}
  </style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div id="summary" class="summary"></div>
  <div class="toolbar">
    <label>Página <select id="pageSelect"></select></label>
    <label>Zoom <input id="zoom" type="range" min="20" max="200" value="80" /> <span id="zoomVal">80%</span></label>
    <label><input id="onlyChanged" type="checkbox" /> solo páginas con cambios</label>
    <button id="toggleDiff">Alternar diff/overlay</button>
    <button id="syncToggle">Sync scroll: ON</button>
  </div>
</header>
<main>
  <nav id="pageList"></nav>
  <section class="viewer">
    <div id="pageMeta" class="pageMeta"></div>
    <div id="notes" class="notes"></div>
    <div class="grid">
      <div class="pane"><h2>Original</h2><div class="scrollbox"><img id="origImg" class="pageImg" /></div></div>
      <div class="pane"><h2>Procesado</h2><div class="scrollbox"><img id="procImg" class="pageImg" /></div></div>
      <div class="pane"><h2 id="thirdTitle">Diff</h2><div class="scrollbox"><img id="thirdImg" class="pageImg" /></div></div>
    </div>
  </section>
</main>
<script>
let DATA = null;
let currentPage = 1;
let showOverlay = false;
let syncScroll = true;
let zoom = 0.80;

function pct(x) {{ return (100*x).toFixed(3) + '%'; }}
function riskClass(r) {{ return 'risk-' + r; }}
function pageLabel(p) {{ return `Pág. ${{p.page}} — ${{p.risk.toUpperCase()}} — ${{pct(p.changed_pixels_pct)}}`; }}

async function load() {{
  const res = await fetch('summary.json');
  DATA = await res.json();
  renderSummary();
  renderPageList();
  showPage(1);
}}
function renderSummary() {{
  const s = DATA.summary;
  document.getElementById('summary').innerHTML = `
    <span class="pill">Original: ${{s.original_pages}} págs</span>
    <span class="pill">Procesado: ${{s.processed_pages}} págs</span>
    <span class="pill">Cambiadas: ${{s.pages_changed}}</span>
    <span class="pill">Riesgo alto: ${{s.high_risk_pages.length}}</span>
    <span class="pill">DPI: ${{s.dpi}}</span>
    <span class="pill">OpenCV: ${{s.opencv_enabled ? 'sí':'no'}}</span>`;
}}
function filteredPages() {{
  const only = document.getElementById('onlyChanged').checked;
  return DATA.pages.filter(p => !only || p.status !== 'identical');
}}
function renderPageList() {{
  const list = document.getElementById('pageList');
  const select = document.getElementById('pageSelect');
  list.innerHTML = '';
  select.innerHTML = '';
  for (const p of filteredPages()) {{
    const b = document.createElement('button');
    b.className = 'pageBtn ' + (p.page === currentPage ? 'active ' : '') + riskClass(p.risk);
    b.textContent = pageLabel(p);
    b.onclick = () => showPage(p.page);
    list.appendChild(b);
    const opt = document.createElement('option'); opt.value = p.page; opt.textContent = pageLabel(p);
    select.appendChild(opt);
  }}
  select.value = currentPage;
}}
function imgOrBlank(path) {{ return path || ''; }}
function showPage(n) {{
  currentPage = n;
  const p = DATA.pages.find(x => x.page === n) || DATA.pages[0];
  document.getElementById('origImg').src = imgOrBlank(p.original_image);
  document.getElementById('procImg').src = imgOrBlank(p.processed_image);
  document.getElementById('thirdImg').src = imgOrBlank(showOverlay ? p.overlay_image : p.diff_image);
  document.getElementById('thirdTitle').textContent = showOverlay ? 'Overlay' : 'Diff';
  document.getElementById('pageMeta').innerHTML = `
    <span class="pill">Página ${{p.page}}</span>
    <span class="pill ${{riskClass(p.risk)}}">Riesgo: ${{p.risk}}</span>
    <span class="pill">Cambio: ${{pct(p.changed_pixels_pct)}}</span>
    <span class="pill">Regiones: ${{p.changed_regions}}</span>
    <span class="pill">Región mayor: ${{pct(p.largest_region_pct)}}</span>`;
  document.getElementById('notes').textContent = (p.notes || []).join('\n');
  applyZoom(); renderPageList();
}}
function applyZoom() {{
  for (const id of ['origImg','procImg','thirdImg']) {{
    const img = document.getElementById(id);
    img.style.width = (zoom * 100) + '%';
  }}
}}
document.getElementById('pageSelect').onchange = e => showPage(Number(e.target.value));
document.getElementById('onlyChanged').onchange = renderPageList;
document.getElementById('toggleDiff').onclick = () => {{ showOverlay = !showOverlay; showPage(currentPage); }};
document.getElementById('syncToggle').onclick = () => {{ syncScroll = !syncScroll; document.getElementById('syncToggle').textContent = 'Sync scroll: ' + (syncScroll ? 'ON':'OFF'); }};
document.getElementById('zoom').oninput = e => {{ zoom = Number(e.target.value)/100; document.getElementById('zoomVal').textContent = e.target.value + '%'; applyZoom(); }};
for (const box of document.querySelectorAll('.scrollbox')) {{
  box.addEventListener('scroll', e => {{
    if (!syncScroll) return;
    const src = e.target;
    for (const other of document.querySelectorAll('.scrollbox')) {{
      if (other !== src) {{ other.scrollTop = src.scrollTop; other.scrollLeft = src.scrollLeft; }}
    }}
  }});
}}
load();
</script>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")


def serve(out_dir: Path, host: str, port: int) -> None:
    try:
        from flask import Flask, send_from_directory
    except Exception as exc:
        raise SystemExit("Missing dependency: flask. Install with: pip install flask") from exc
    app = Flask(__name__)

    @app.route("/")
    def index():
        return send_from_directory(out_dir, "index.html")

    @app.route("/<path:path>")
    def static_files(path: str):
        return send_from_directory(out_dir, path)

    print(f"Serving {out_dir} at http://{host}:{port}/")
    app.run(host=host, port=port, debug=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Visual PDF diff + web viewer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_compare = sub.add_parser("compare", help="Render two PDFs and generate visual diff viewer")
    p_compare.add_argument("original", type=Path)
    p_compare.add_argument("processed", type=Path)
    p_compare.add_argument("--out", type=Path, required=True)
    p_compare.add_argument("--dpi", type=int, default=160)
    p_compare.add_argument("--threshold", type=int, default=35, help="Pixel difference threshold 0-255")
    p_compare.add_argument("--min-region-area-px", type=int, default=100, help="Ignore tiny diff boxes below this area")
    p_compare.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    p_compare.add_argument("--title", default=None)

    p_serve = sub.add_parser("serve", help="Serve an existing diff output directory")
    p_serve.add_argument("out", type=Path)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)

    args = parser.parse_args(argv)
    if args.cmd == "compare":
        if not args.original.exists():
            raise SystemExit(f"Original PDF not found: {args.original}")
        if not args.processed.exists():
            raise SystemExit(f"Processed PDF not found: {args.processed}")
        data = compare_pdfs(args.original, args.processed, args.out, args.dpi, args.threshold,
                            args.min_region_area_px, args.workers, args.title)
        print(json.dumps(data["summary"], indent=2, ensure_ascii=False))
        print(f"\nViewer: {args.out / 'index.html'}")
        print(f"Serve with: python {Path(__file__).name} serve {args.out} --host 0.0.0.0 --port 8787")
        return 0
    if args.cmd == "serve":
        if not (args.out / "index.html").exists():
            raise SystemExit(f"Diff output does not contain index.html: {args.out}")
        serve(args.out, args.host, args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
