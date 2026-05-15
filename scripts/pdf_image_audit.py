#!/usr/bin/env python3
"""
PDF Image Audit & Strip — Detect and remove repeated/duplicate images and text watermarks.

  Before processing tender PDFs through an LLM pipeline, use this tool to:
  1. AUDIT:   Identify logos, headers, stamps, decorative images, AND text watermarks
  2. STRIP:   Remove them + re-optimize (garbage collect, deflate, re-linearize)
  3. EXTRACT: Clean markdown text with noise filtered out (no PDF modification)

  Image categories detected:
    HIGH_FREQ   — Same image on ≥ N pages (headers/logos)
    DUPLICATE   — Same pixel content stored as different xrefs (inefficient encoding)
    PERCEPTUAL_DUP — Visually identical images with different hashes (recompressed logos)
    TINY        — Images smaller than threshold (bullets, decorations, icons)
    SIGNATURE   — Same image repeated on 2-N pages (signatures, stamps, backgrounds)
    DIGITAL_SIG — PDF digital signature widgets ("Firmado Digitalmente por: ...")

  Text categories detected:
    TEXT_REPEAT  — Same text appearing on ≥ threshold% of pages (headers, footers, watermarks)
    PAGE_NUMBER  — Auto-detected page numbering ("Página N de M", "Page N of M", "N/M")

  Removal methods:
    Image: Content stream surgery — removes the /Name Do operator from page content streams.
           This is cleaner than replacing with 1x1 PNG: no new objects created, no phantom images.
    Text:  Redaction — applies white-filled redaction annotations over the text spans, then
           applies redactions with PDF_REDACT_IMAGE_NONE to avoid touching images.

  After stripping, the PDF is re-saved with full optimization (garbage=4, deflate, linear)
  which often produces a SMALLER file than the original despite the modifications.

  Usage:
    # Audit only (report what's duplicated/repeated)
    python pdf_image_audit.py input.pdf

    # Audit + strip images + text → optimized PDF (usually smaller than original!)
    python pdf_image_audit.py input.pdf --strip -o input_clean.pdf

  # Strip only images (skip text watermark removal)
  python pdf_image_audit.py input.pdf --strip --no-text

  # Strip only text watermarks (skip image removal)
  python pdf_image_audit.py input.pdf --strip --text-only

  # Custom thresholds
  python pdf_image_audit.py input.pdf --strip --min-freq 3 --tiny-max 15

  # Strip specific categories only
  python pdf_image_audit.py input.pdf --strip --categories HIGH_FREQ,TEXT_REPEAT

Output:
  - Console summary table
  - JSON report (--report path)
  - Stripped PDF (--output path)
"""
import os
import sys
import hashlib
import json
import re
import struct
import zlib
import io
import argparse
from collections import defaultdict

# pip install path for restricted containers
sys.path.insert(0, os.path.expanduser('~/.local/lib/python3.13/site-packages'))

import pymupdf


# ─── Minimal 1x1 white PNG (fallback only) ───────────────────────────────────

def _make_1x1_png():
    """Create a valid 1x1 white PNG (69 bytes)."""
    sig = b'\x89PNG\r\n\x1a\n'
    def chunk(ct, d):
        c = ct + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b'\x00\xff\xff\xff')
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


# ─── Text watermark detection ────────────────────────────────────────────────

# Patterns that are DEFINITELY page numbers (require explicit marker word)
_PAGE_NUMBER_RE = re.compile(
    r'^[-–—\s]*'
    r'('
    r'p[aá]gina\s+\d+(\s+de\s+\d+)?'    # "Página 5" / "Página 5 de 100"
    r'|p[aá]g\.?\s*\d+(\s+de\s+\d+)?'    # "Pág. 5" / "Pág. 5 de 100"
    r'|page\s+\d+(\s+of\s+\d+)?'         # "Page 5" / "Page 5 of 100"
    r'|hoja\s+\d+(\s+de\s+\d+)?'         # "Hoja 5" / "Hoja 5 de 100"
    r'|sheet\s+\d+(\s+of\s+\d+)?'        # "Sheet 5" / "Sheet 5 of 100"
    r')'
    r'[-–—\s]*$',
    re.IGNORECASE
)


def _get_page_image_names(doc, xref):
    """Get all resource names for a given image xref across the document."""
    names = set()
    for page in doc:
        for img in page.get_images(full=True):
            if img[0] == xref:
                names.add(img[7])  # img[7] is the resource name (e.g. "Im0")
    return names


def _normalize_for_clustering(text):
    """Replace variable parts (numbers, dates) with placeholders for clustering."""
    t = " ".join(text.split())
    t = re.sub(r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b', 'DATE', t)
    t = re.sub(r'\b\d{1,2}:\d{2}(:\d{2})?\b', 'TIME', t)
    t = re.sub(r'\b\d{1,4}\b', 'N', t)
    return t


def _detect_text_watermarks(doc, header_zone=0.12, footer_zone=0.88,
                           left_margin=0.08, right_margin=0.90,
                           threshold=0.3, min_pages=5):
    """
    Detect text that repeats on many pages in non-content zones.

    Zones (any text OUTSIDE these is "content" and ignored):
    - header: top N% of page (y < header_zone * page_height)
    - footer: bottom N% of page (y > footer_zone * page_height)
    - left_margin: left edge (x < left_margin * page_width)
    - right_margin: right edge (x > right_margin * page_width)

    The key criterion is SPATIAL REPETITION: same normalized text at the
    same zone on ≥threshold pages = decorative/watermark, regardless of
    whether it's horizontal, rotated, or any other orientation.

    Returns list of candidate dicts for removal_candidates.
    """
    total_pages = len(doc)

    # Collect lines in non-content zones across ALL pages
    zone_lines = []  # [(page, zone, y, text, norm_text)]

    for i in range(total_pages):
        page = doc[i]
        ph = page.rect.height
        pw = page.rect.width
        try:
            blocks = page.get_text("dict")["blocks"]
        except:
            continue

        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text or len(text) < 2:
                    continue
                y = line["bbox"][1]
                x = line["bbox"][0]  # left edge of line
                norm = _normalize_for_clustering(text)

                # Classify zone: header > footer > margins > content
                if y < ph * header_zone:
                    zone = "header"
                elif y > ph * footer_zone:
                    zone = "footer"
                elif x < pw * left_margin:
                    zone = "left_margin"
                elif x > pw * right_margin:
                    zone = "right_margin"
                else:
                    continue  # content zone — skip

                zone_lines.append((i + 1, zone, y, text, norm))

    if not zone_lines:
        return []

    # Group by (zone, normalized_text) — this clusters "Página 1 de 106" with
    # "Página 50 de 106" because both normalize to "Página N de N"
    norm_groups = defaultdict(list)
    for pg, zone, y, text, norm in zone_lines:
        norm_groups[(zone, norm)].append((pg, y, text))

    # Find groups meeting threshold
    candidates = []

    zone_labels = {
        "header": "Header", "footer": "Footer",
        "left_margin": "Left margin", "right_margin": "Right margin"
    }

    for (zone, norm), entries in norm_groups.items():
        pages = sorted(set(pg for pg, _, _ in entries))
        page_count = len(pages)
        freq = page_count / total_pages

        if page_count < min_pages or freq < threshold:
            continue

        # Classify
        sample_texts = [text for _, _, text in entries[:3]]

        # Check if it's a page number pattern
        if re.search(r'P[aá]gina|p[aá]gina|Page|page', norm) and re.search(r'N.*de.*N|N.*of.*N|N/N', norm):
            category = "PAGE_NUMBER"
            label = f'Page numbering: "{sample_texts[0]}" on {page_count}/{total_pages} pages'
        else:
            category = "TEXT_REPEAT"
            zl = zone_labels.get(zone, zone.title())
            label = f'{zl} text: "{sample_texts[0][:50]}" on {page_count}/{total_pages} pages'

        candidates.append({
            "text": norm,
            "samples": sample_texts,
            "zone": zone,
            "page_count": page_count,
            "freq": freq,
            "pages_sample": pages[:10],
            "category": category,
            "label": label,
            "xrefs": [],
            "width": 0, "height": 0,
        })

    # Sort by page_count descending
    candidates.sort(key=lambda x: -x["page_count"])
    return candidates


# ─── Image audit ─────────────────────────────────────────────────────────────

def _detect_drawing_watermarks(doc, threshold=0.5, min_pages=10, quantize=1.0):
    """
    Detect vector drawings that repeat at the same position on many pages.

    These are typically decorative borders, divider lines, or frames that
    are part of the page template (e.g., a vertical line in the right margin,
    a box around page numbers).

    Drawings at the same quantized rect position on ≥threshold pages are
    flagged for removal.

    Returns list of candidate dicts for removal_candidates.
    """
    total_pages = len(doc)
    if total_pages < min_pages:
        return []

    # Collect all drawings across all pages
    # Key: (quantized_rect, fill_color, item_types) → [page_indices]
    from collections import defaultdict
    drawing_groups = defaultdict(list)  # key → [(page_idx, rect)]

    for i in range(total_pages):
        page = doc[i]
        try:
            drawings = page.get_drawings()
        except Exception:
            continue

        # Safety: skip pages with excessive drawings (e.g., CAD/blueprint pages)
        if len(drawings) > 500:
            continue

        for d in drawings:
            r = d.get("rect")
            if not r:
                continue
            # Quantize rect
            q = (round(r.x0 / quantize) * quantize,
                 round(r.y0 / quantize) * quantize,
                 round(r.x1 / quantize) * quantize,
                 round(r.y1 / quantize) * quantize)
            # Item types signature (e.g., ('re',) for rectangle, ('l',) for line)
            item_sig = tuple(it[0] for it in d.get("items", []))
            fill = d.get("fill")
            color = d.get("color")
            key = (q, fill, color, item_sig)
            drawing_groups[key].append((i, pymupdf.Rect(r)))

    # ── Sibling key fusion ──────────────────────────────────────────────
    # Elements that alternate position (e.g., odd/even pages) end up in
    # separate keys that never individually reach the threshold.
    #
    # Pass 1: merge keys that share fill, color, item_types and are within
    #         2pt of each other (handles sub-pixel position jitter).
    # Pass 2: merge keys that share fill, color, item_types and have the
    #         same SIZE (±2pt) regardless of position — handles elements
    #         that mirror between left/right or top/bottom of the page.
    MERGE_TOLERANCE = 2.0  # pt
    keys = list(drawing_groups.keys())
    merged = set()       # keys already absorbed into another

    # Safety: skip fusion entirely if too many unique keys (e.g., CAD/blueprint pages)
    # O(n²) fusion with 135K keys would take hours; 5K keys ≈ 25M comparisons is ~2s
    MAX_FUSION_KEYS = 5000
    skip_fusion = len(keys) > MAX_FUSION_KEYS

    def _structural_match(a, b):
        """Same fill, color, item_types."""
        return a[1] == b[1] and a[2] == b[2] and a[3] == b[3]

    def _spatial_close(q_a, q_b, tol):
        """All 4 coords within tol."""
        return all(abs(a - b) <= tol for a, b in zip(q_a, q_b))

    def _same_size(q_a, q_b, tol):
        """Width and height within tol (position-independent)."""
        w_a, h_a = q_a[2] - q_a[0], q_a[3] - q_a[1]
        w_b, h_b = q_b[2] - q_b[0], q_b[3] - q_b[1]
        return abs(w_a - w_b) <= tol and abs(h_a - h_b) <= tol

    def _merge(b_key, a_key):
        drawing_groups[a_key].extend(drawing_groups[b_key])
        merged.add(b_key)

    # Pass 1: position-based fusion (nearby siblings)
    if not skip_fusion:
        for i_a in range(len(keys)):
            if keys[i_a] in merged:
                continue
            for i_b in range(i_a + 1, len(keys)):
                if keys[i_b] in merged:
                    continue
                if _structural_match(keys[i_a], keys[i_b]) and \
                   _spatial_close(keys[i_a][0], keys[i_b][0], MERGE_TOLERANCE):
                    _merge(keys[i_b], keys[i_a])

    # Pass 2: size-based fusion (mirror siblings — same visual, diff position)
    # Only merge if combined page count would reach threshold
    if not skip_fusion:
        for i_a in range(len(keys)):
            if keys[i_a] in merged:
                continue
            q_a, _, _, _ = keys[i_a]
            pages_a = set(p[0] for p in drawing_groups[keys[i_a]])
            for i_b in range(i_a + 1, len(keys)):
                if keys[i_b] in merged:
                    continue
                if not _structural_match(keys[i_a], keys[i_b]):
                    continue
                q_b, _, _, _ = keys[i_b]
                if not _same_size(q_a, q_b, MERGE_TOLERANCE):
                    continue
                pages_b = set(p[0] for p in drawing_groups[keys[i_b]])
                combined = len(pages_a | pages_b)
                # Only fuse if combined would pass threshold
                if combined / total_pages >= threshold and combined >= min_pages:
                    _merge(keys[i_b], keys[i_a])
                    pages_a = pages_a | pages_b  # update for next iteration

    # Filter: only drawings on ≥threshold pages and ≥min_pages
    candidates = []
    for key, occurrences in drawing_groups.items():
        if key in merged:
            continue
        page_set = set(p[0] for p in occurrences)
        page_count = len(page_set)
        freq = page_count / total_pages

        if freq >= threshold and page_count >= min_pages:
            q_rect, fill, color, item_sig = key
            w = q_rect[2] - q_rect[0]
            h = q_rect[3] - q_rect[1]

            # Build description
            item_names = {"re": "rectangle", "l": "line", "c": "curve", "qu": "quadratic"}
            item_desc = ", ".join(item_names.get(it, it) for it in item_sig)

            if w < 2 and h < 2:
                size_desc = f"point at ({q_rect[0]:.0f},{q_rect[1]:.0f})"
            elif w < 2:
                size_desc = f"vertical line ({h:.0f}pt) at x={q_rect[0]:.0f}"
            elif h < 2:
                size_desc = f"horizontal line ({w:.0f}pt) at y={q_rect[1]:.0f}"
            else:
                size_desc = f"rectangle ({w:.0f}x{h:.0f}pt) at ({q_rect[0]:.0f},{q_rect[1]:.0f})"

            candidates.append({
                "category": "DRAWING_REPEAT",
                "quantized_rect": q_rect,
                "fill": fill,
                "color": color,
                "item_types": list(item_sig),
                "page_count": page_count,
                "freq": round(freq, 4),
                "pages_sample": sorted(page_set)[:10],
                "occurrences": [(p, (r.x0, r.y0, r.x1, r.y1)) for p, r in occurrences],
                "label": f"{item_desc} {size_desc} on {page_count}/{total_pages} pages"
            })

    return candidates


# ─── Page content analysis (for plan/diagram detection) ──────────────────────

def analyze_page_contents(pdf_path):
    """
    Analyze every page of a PDF and return per-page content metrics:
    dimensions, text density, image count, vector drawing count/area.

    This is used by the clean step to produce a {stem}_page_analysis.json
    that downstream plan detection (Paso 1.2b) can consume directly,
    avoiding a separate pass.

    Returns dict: {"pages": [...], "summary": {...}}
    """
    doc = pymupdf.open(pdf_path)
    pages = []
    for i in range(len(doc)):
        page = doc[i]
        r = page.rect
        w, h = float(r.width), float(r.height)
        area = w * h

        # Text metrics
        text = page.get_text("text")
        text_chars = len(text)
        blocks = page.get_text("dict")["blocks"]
        text_blocks = sum(1 for b in blocks if b["type"] == 0)
        image_blocks = sum(1 for b in blocks if b["type"] == 1)

        # Image metrics
        images = page.get_images(full=True)
        image_count = len(images)

        # Vector drawing metrics
        drawings = page.get_drawings()
        drawing_count = len(drawings)
        drawing_area = sum(
            (d["rect"].width * d["rect"].height)
            for d in drawings if d.get("rect")
        )
        drawing_area_ratio = round(drawing_area / area, 4) if area > 0 else 0.0

        # Text density
        text_density = round(text_chars / area, 6) if area > 0 else 0.0

        # Image area coverage (approximate)
        image_area = 0.0
        for b in blocks:
            if b["type"] == 1:  # image block
                bbox = b.get("bbox", (0, 0, 0, 0))
                image_area += (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        image_area_ratio = round(image_area / area, 4) if area > 0 else 0.0

        # Determine content_dominant
        content_dominant = "text"
        if drawing_count > 1000 or drawing_area_ratio > 0.3:
            content_dominant = "vector_drawing"
        elif image_area_ratio > 0.4 or (image_count > 3 and image_area_ratio > 0.25):
            content_dominant = "image_heavy"
        elif drawing_count > 100 and image_count > 2 and text_density < 0.005:
            content_dominant = "mixed"

        # Plan candidate signals
        signals = []
        if drawing_count > 1000:
            signals.append("high_drawing_count")
        if drawing_area_ratio > 0.3:
            signals.append("high_drawing_ratio")
        if image_area_ratio > 0.4:
            signals.append("image_heavy")
        if text_density < 0.002 and area > 100000:
            signals.append("very_low_text_density")
        if drawing_count > 5000 and image_count <= 2 and text_density < 0.005:
            signals.append("autocad_like")
        if image_count > 5 and text_density < 0.002:
            signals.append("many_images_low_text")

        pages.append({
            "page": i + 1,
            "width_pt": round(w, 2),
            "height_pt": round(h, 2),
            "area_pt2": round(area, 2),
            "orientation": "landscape" if w > h else "portrait",
            "text_char_count": text_chars,
            "text_block_count": text_blocks,
            "image_count": image_count,
            "image_block_count": image_blocks,
            "image_area_ratio": image_area_ratio,
            "drawing_count": drawing_count,
            "drawing_area_ratio": drawing_area_ratio,
            "text_density_chars_per_pt2": text_density,
            "content_dominant": content_dominant,
            "plan_candidate_signals": signals,
        })

    # Post-process: add size-based signals against median
    import statistics
    if pages:
        areas = [p["area_pt2"] for p in pages]
        median_area = statistics.median(areas)
        for p in pages:
            ratio = p["area_pt2"] / median_area if median_area > 0 else 1.0
            if ratio >= 1.4:
                p["area_ratio_vs_median"] = round(ratio, 3)
                p["plan_candidate_signals"].append("large_page")
            # Refresh content_dominant with size context
            if "large_page" in p["plan_candidate_signals"]:
                if p["content_dominant"] == "text" and p["text_density_chars_per_pt2"] < 0.01:
                    p["content_dominant"] = "low_density_large_page"

    summary = {
        "source_pdf": pdf_path,
        "page_count": len(pages),
        "median_area_pt2": round(statistics.median([p["area_pt2"] for p in pages]), 2) if pages else 0,
        "pages_with_signals": sum(1 for p in pages if p["plan_candidate_signals"]),
        "signal_types": sorted(set(s for p in pages for s in p["plan_candidate_signals"])),
    }

    return {"pages": pages, "summary": summary}


def analyze_pdf_images(pdf_path, min_freq=5, tiny_max_px=20,
                       text_threshold=0.5, text_min_pages=10,
                       header_zone=0.12, footer_zone=0.88,
                       left_margin=0.08, right_margin=0.90):
    """
    Scan a PDF and classify images AND text by duplication/repetition patterns.

    Args:
        min_freq: Min page count for HIGH_FREQ image category (default 5)
        tiny_max_px: Max dimension for TINY image category (default 20)
        text_threshold: Min % of pages for TEXT_REPEAT/PAGE_NUMBER (default 0.5 = 50%)
        text_min_pages: Min absolute page count for text candidates (default 10)

    Returns dict with: summary, removal_candidates (images + text), content_images
    """
    doc = pymupdf.open(pdf_path)
    file_size = os.path.getsize(pdf_path)

    xref_hashes = {}
    xref_sizes = {}
    xref_pages = defaultdict(set)
    hash_to_xrefs = defaultdict(list)
    xref_phashes = {}       # perceptual hash for visual dedup

    for i in range(len(doc)):
        page = doc[i]

        for img in page.get_images(full=True):
            xref = img[0]
            xref_pages[xref].add(i + 1)
            if xref not in xref_hashes:
                try:
                    pix = pymupdf.Pixmap(doc, xref)
                    raw = pix.tobytes("png")
                    h = hashlib.sha256(raw).hexdigest()[:16]
                    xref_hashes[xref] = h
                    xref_sizes[xref] = (pix.width, pix.height)
                    hash_to_xrefs[h].append(xref)

                    # Compute perceptual hash (catches recompressed/re-encoded duplicates)
                    try:
                        from PIL import Image
                        import imagehash
                        pil_img = Image.open(io.BytesIO(raw))
                        xref_phashes[xref] = imagehash.phash(pil_img, hash_size=16)
                    except Exception:
                        xref_phashes[xref] = None

                    pix = None
                except Exception as e:
                    xref_hashes[xref] = f"ERROR:{e}"
                    xref_sizes[xref] = (0, 0)

    total_img_refs = sum(len(p) for p in xref_pages.values())
    unique_hashes = len(set(xref_hashes.values()) - {h for h in xref_hashes.values() if str(h).startswith("ERROR")})

    removal_candidates = []
    dedup_map = {}
    flagged_xrefs = set()

    # 1. HIGH_FREQ: Same xref referenced from many pages
    for xr, pages in xref_pages.items():
        if len(pages) >= min_freq:
            w, h = xref_sizes.get(xr, (0, 0))
            removal_candidates.append({
                "xrefs": [xr],
                "hash": xref_hashes.get(xr, "?"),
                "width": w, "height": h,
                "page_count": len(pages),
                "pages_sample": sorted(pages)[:10],
                "category": "HIGH_FREQ",
                "label": _label_for_size(w, h, len(pages))
            })
            flagged_xrefs.add(xr)

    # 2. DUPLICATE: Same pixel content, different xrefs
    for h, xrefs in hash_to_xrefs.items():
        if len(xrefs) > 1 and not str(h).startswith("ERROR"):
            keep = max(xrefs, key=lambda x: len(xref_pages[x]))
            for xr in xrefs:
                if xr != keep:
                    dedup_map[xr] = keep

            if not any(xr in flagged_xrefs for xr in xrefs):
                w, ht = xref_sizes.get(keep, (0, 0))
                all_pages = set()
                for xr in xrefs:
                    all_pages.update(xref_pages[xr])
                removal_candidates.append({
                    "xrefs": xrefs,
                    "hash": h,
                    "width": w, "height": ht,
                    "page_count": len(all_pages),
                    "pages_sample": sorted(all_pages)[:10],
                    "category": "DUPLICATE",
                    "label": f"{len(xrefs)} copies of same {w}x{ht} image",
                    "keep_xref": keep,
                    "drop_xrefs": [x for x in xrefs if x != keep]
                })
                flagged_xrefs.update(xrefs)

    # 2b. PERCEPTUAL_DUP: Visually identical images with different SHA256
    #     (e.g., same logo recompressed as JPEG on some pages, PNG on others)
    #     Uses perceptual hashing (pHash) — tolerant to recompression,
    #     minor resize, gamma changes. Hamming distance ≤8 = visually identical.
    #
    #     IMPORTANT: Only flags images where BOTH dimensions are ≤ max_side
    #     (default 300px). Large images are almost always content (photos,
    #     drawings, diagrams), never logos/stamps/signatures.
    try:
        from PIL import Image
        import imagehash

        # Group unflagged xrefs by perceptual hash (skip large images)
        phash_groups = defaultdict(list)
        for xr in xref_pages:
            if xr in flagged_xrefs:
                continue
            w, h = xref_sizes.get(xr, (0, 0))
            # Skip large images — they're content, not noise
            if w > 300 or h > 300:
                continue
            ph = xref_phashes.get(xr)
            if ph is not None:
                phash_groups[str(ph)].append(xr)

        # Check each group: if ≥2 xrefs look identical visually
        for ph_str, xrefs in phash_groups.items():
            if len(xrefs) < 2:
                continue

            # Cross-check: merge groups with Hamming distance ≤ threshold
            # (pHash with hash_size=16 produces 256-bit hash, threshold 12 ≈ 95% similar)
            merged = set(xrefs)
            for other_ph, other_xrefs in phash_groups.items():
                if other_ph == ph_str:
                    continue
                try:
                    dist = imagehash.hex_to_hash(ph_str) - imagehash.hex_to_hash(other_ph)
                    if dist <= 12:
                        merged.update(other_xrefs)
                except Exception:
                    pass

            merged = [xr for xr in merged if xr not in flagged_xrefs]
            if len(merged) < 2:
                continue

            w, ht = xref_sizes.get(merged[0], (0, 0))
            all_pages = set()
            for xr in merged:
                all_pages.update(xref_pages.get(xr, set()))

            # Keep the xref used on most pages, flag the rest
            keep = max(merged, key=lambda x: len(xref_pages[x]))
            drops = [x for x in merged if x != keep]

            removal_candidates.append({
                "xrefs": merged,
                "hash": f"phash:{ph_str[:16]}",
                "width": w, "height": ht,
                "page_count": len(all_pages),
                "pages_sample": sorted(all_pages)[:10],
                "category": "PERCEPTUAL_DUP",
                "label": f"Visual duplicate ({w}x{ht}px, {len(merged)} variants on {len(all_pages)} pages)",
                "keep_xref": keep,
                "drop_xrefs": drops
            })
            flagged_xrefs.update(drops)
    except ImportError:
        pass  # imagehash not available, skip perceptual dedup

    # 3. TINY: Images below pixel threshold
    for xr in xref_pages:
        w, h = xref_sizes.get(xr, (0, 0))
        if w < tiny_max_px and h < tiny_max_px and xr not in flagged_xrefs:
            removal_candidates.append({
                "xrefs": [xr],
                "hash": xref_hashes.get(xr, "?"),
                "width": w, "height": h,
                "page_count": len(xref_pages[xr]),
                "pages_sample": sorted(xref_pages[xr])[:5],
                "category": "TINY",
                "label": f"Decoration ({w}x{h}px)"
            })
            flagged_xrefs.add(xr)

    # 4. SIGNATURE/STAMP: Same image on ≥2 pages but < min_freq
    #    These are signatures, stamps, or backgrounds that repeat but not
    #    enough to trigger HIGH_FREQ. Position and size don't matter —
    #    if the same pixel content appears on multiple pages, it's noise.
    #
    #    EXCEPTION: if a sibling xref with the same hash was already flagged
    #    as HIGH_FREQ, these are just duplicate encodings of the same header/logo.
    #    Add them to the HIGH_FREQ candidate instead of creating a new SIGNATURE.
    high_freq_hashes = set()
    high_freq_by_hash = {}  # hash -> candidate dict
    for c in removal_candidates:
        if c["category"] == "HIGH_FREQ":
            high_freq_hashes.add(c.get("hash"))
            high_freq_by_hash[c.get("hash")] = c

    for xr, pages in xref_pages.items():
        if xr in flagged_xrefs:
            continue
        # Only check xrefs that haven't been flagged yet
        h = xref_hashes.get(xr, "?")
        if str(h).startswith("ERROR"):
            continue
        # If this hash is already a HIGH_FREQ header, merge xref into it
        # (same logo/header stored as a separate xref on a few extra pages)
        if h in high_freq_hashes:
            hf_candidate = high_freq_by_hash[h]
            if xr not in hf_candidate["xrefs"]:
                hf_candidate["xrefs"].append(xr)
                extra_pages = xref_pages.get(xr, set())
                hf_candidate["pages_sample"] = sorted(
                    set(hf_candidate.get("pages_sample", [])) | extra_pages
                )[:10]
                hf_candidate["page_count"] += len(extra_pages)
            flagged_xrefs.add(xr)
            continue
        # Find all xrefs with this same hash (may have been partially flagged)
        siblings = hash_to_xrefs.get(h, [])
        total_pages = sum(len(xref_pages.get(s, set())) for s in siblings if s not in flagged_xrefs)
        if total_pages >= 2 and total_pages < min_freq:
            unflagged = [s for s in siblings if s not in flagged_xrefs]
            if unflagged:
                w, ht = xref_sizes.get(unflagged[0], (0, 0))
                all_pages = set()
                for s in unflagged:
                    all_pages.update(xref_pages.get(s, set()))
                removal_candidates.append({
                    "xrefs": unflagged,
                    "hash": h,
                    "width": w, "height": ht,
                    "page_count": len(all_pages),
                    "pages_sample": sorted(all_pages)[:10],
                    "category": "SIGNATURE",
                    "label": f"Signature/stamp/bg ({w}x{ht}px, same image on {len(all_pages)} pages)"
                })
                flagged_xrefs.update(unflagged)

    # 5. DIGITAL_SIG: PDF digital signature widgets (appearance streams)
    #    These are Form XObjects attached to Widget annotations, not regular images.
    #    They show as "Firmado Digitalmente por: ..." blocks.
    digital_sigs = []
    for i in range(len(doc)):
        page = doc[i]
        for w in (page.widgets() or []):
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                digital_sigs.append({
                    "xrefs": [w.xref],
                    "hash": "widget",
                    "width": int(w.rect.width),
                    "height": int(w.rect.height),
                    "page_count": 1,
                    "pages_sample": [i + 1],
                    "category": "DIGITAL_SIG",
                    "label": f"Digital signature '{w.field_name}' ({int(w.rect.width)}x{int(w.rect.height)}px, page {i+1})",
                    "widget_xref": w.xref,
                    "rect": (w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1),
                    "page_idx": i
                })
    if digital_sigs:
        removal_candidates.extend(digital_sigs)

    # 6. TEXT_REPEAT + PAGE_NUMBER: Text watermarks and headers
    text_candidates = _detect_text_watermarks(doc, header_zone=header_zone,
                                               footer_zone=footer_zone,
                                               left_margin=left_margin,
                                               right_margin=right_margin,
                                               threshold=text_threshold,
                                               min_pages=text_min_pages)
    removal_candidates.extend(text_candidates)

    # 7. DRAWING_REPEAT: Vector drawings (rectangles, lines) at same position on many pages
    drawing_candidates = _detect_drawing_watermarks(doc, threshold=text_threshold,
                                                     min_pages=text_min_pages)
    removal_candidates.extend(drawing_candidates)

    # Content images (NOT flagged)
    content_images = []
    for xr in xref_pages:
        if xr not in flagged_xrefs:
            w, h = xref_sizes.get(xr, (0, 0))
            content_images.append({
                "xref": xr,
                "hash": xref_hashes.get(xr, "?"),
                "width": w, "height": h,
                "pages": sorted(xref_pages[xr])
            })

    cat_counts = defaultdict(int)
    for c in removal_candidates:
        if c["category"] in ("TEXT_REPEAT", "PAGE_NUMBER", "DRAWING_REPEAT"):
            cat_counts[c["category"]] += 1
        else:
            cat_counts[c["category"]] += len(c["xrefs"])

    text_flagged = len([c for c in removal_candidates if c["category"] in ("TEXT_REPEAT", "PAGE_NUMBER")])

    summary = {
        "file": os.path.basename(pdf_path),
        "file_size_bytes": file_size,
        "file_size_human": f"{file_size / 1024 / 1024:.1f} MB",
        "total_pages": len(doc),
        "total_image_xrefs": len(xref_pages),
        "total_image_refs": total_img_refs,
        "unique_image_hashes": unique_hashes,
        "images_flagged": len(flagged_xrefs),
        "text_flagged": text_flagged,
        "content_images_remaining": len(content_images),
        "categories": dict(cat_counts),
        "zones": {
            "header_zone": header_zone,
            "footer_zone": footer_zone,
            "left_margin": left_margin,
            "right_margin": right_margin
        }
    }

    doc.close()

    return {
        "summary": summary,
        "removal_candidates": removal_candidates,
        "dedup_map": {str(k): v for k, v in dedup_map.items()},
        "content_images": content_images,
        "page_analysis": analyze_page_contents(pdf_path) if 'analyze_page_contents' in globals() else None,
    }


def _label_for_size(w, h, page_count):
    if w > 300:
        return f"Header/banner ({w}x{h}, {page_count} pages)"
    elif w > 100:
        return f"Logo ({w}x{h}, {page_count} pages)"
    else:
        return f"Repeated small image ({w}x{h}, {page_count} pages)"


# ─── Strip ────────────────────────────────────────────────────────────────────

def _strip_images_content_stream(doc, report, categories):
    """
    Remove flagged images by editing page content streams.

    For each page, resolves which resource names point to flagged xrefs
    ON THAT PAGE using page.get_images(full=True), then removes only
    those /Name Do operators from the content stream.

    This is page-aware: the same resource name (e.g. "Xop1") may point
    to a flagged xref on one page but a content image on another.

    Returns: (modified_pages, operators_removed)
    """
    xrefs_to_strip = set()
    for c in report["removal_candidates"]:
        if c["category"] in categories and c["category"] not in ("TEXT_REPEAT", "PAGE_NUMBER", "DRAWING_REPEAT"):
            xrefs_to_strip.update(c["xrefs"])

    if not xrefs_to_strip:
        return 0, 0

    modified_pages = 0
    operators_removed = 0

    for page in doc:
        try:
            contents = page.get_contents()
            if not contents:
                continue

            # Build name→xref mapping for THIS page using PyMuPDF structured API
            # (fixes H5: no regex on resource dictionaries — handles nested dicts correctly)
            page_name_to_xref = {}
            try:
                for img in page.get_images(full=True):
                    # img tuple: (xref, smask, w, h, bpc, cs, alt, name, ...)
                    img_xref = img[0]
                    img_name = img[7] if len(img) > 7 else None
                    if img_name:
                        page_name_to_xref[img_name] = img_xref
            except Exception:
                continue

            # Which names on THIS page point to flagged xrefs?
            names_to_remove = set()
            for name, xref in page_name_to_xref.items():
                if xref in xrefs_to_strip:
                    names_to_remove.add(name)

            if not names_to_remove:
                continue

            # Remove /Name Do operators from content streams AND nested Form XObjects.
            # Images may be nested inside Form XObjects (e.g. NxFm5 → /NxImage Do),
            # so we must recurse into Form XObject streams too.
            # (fixes H4: regex is name-specific and only targets known flagged names,
            #  not a general /Name Do pattern — safe for well-formed names from get_images)
            page_modified = False

            # Collect all streams to process: page content streams + Form XObject streams
            streams_to_process = list(contents)  # top-level page content streams
            try:
                for xo in page.get_xobjects():
                    # xo tuple: (xref, name, ...)
                    xo_xref = xo[0]
                    xo_stream = doc.xref_stream(xo_xref)
                    if xo_stream and b' Do' in xo_stream:
                        streams_to_process.append(xo_xref)
            except Exception:
                pass

            for c_xref in streams_to_process:
                try:
                    stream = doc.xref_stream(c_xref)
                    if not stream:
                        continue

                    new_stream = stream
                    for name in names_to_remove:
                        pattern = re.compile(
                            rb'/\s*' + re.escape(name.encode('latin-1')) + rb'\s+Do\b'
                        )
                        new_stream = pattern.sub(b'', new_stream)

                    if new_stream != stream:
                        doc.update_stream(c_xref, new_stream)
                        page_modified = True
                        operators_removed += 1
                except Exception:
                    continue

            if page_modified:
                modified_pages += 1
        except Exception:
            continue

    return modified_pages, operators_removed


def _strip_text_redaction(doc, report, categories):
    """
    Remove flagged text watermarks/headers using redaction annotations.

    Uses normalized text patterns to find matching spans on each page,
    then redacts them WITHOUT fill (transparent) — just removes the text
    operators from the content stream, no white rectangles inserted.

    Zone thresholds are read from report["summary"]["zones"] (set during
    analysis) so strip uses the same zones as detection.

    Returns: (modified_pages, spans_redacted)
    """
    text_candidates = [c for c in report["removal_candidates"]
                       if c["category"] in categories and c["category"] in ("TEXT_REPEAT", "PAGE_NUMBER")]

    if not text_candidates:
        return 0, 0

    # Read zone thresholds from report (M1: consistent with detection)
    zones = report.get("summary", {}).get("zones", {})
    header_zone = zones.get("header_zone", 0.12)
    footer_zone = zones.get("footer_zone", 0.88)
    left_margin = zones.get("left_margin", 0.08)
    right_margin = zones.get("right_margin", 0.90)

    # Build set of normalized patterns to redact
    patterns_to_redact = []
    for c in text_candidates:
        if c["category"] == "PAGE_NUMBER":
            # Use regex for page numbers, with zone constraint (M2)
            zone = c.get("zone")  # "header" or "footer"
            patterns_to_redact.append(("PAGE_NUMBER", c["text"], None, zone))
        else:
            # Store the normalized form for fuzzy matching
            # Also store individual significant words for pre-filtering
            norm = c["text"]
            sig_words = set(w for w in norm.split() if len(w) > 2 and w != "N")
            zone = c.get("zone")  # "header" or "footer"
            patterns_to_redact.append(("TEXT_REPEAT", norm, sig_words, zone))

    modified_pages = 0
    spans_redacted = 0

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            blocks = page.get_text("dict")["blocks"]
        except:
            continue

        rects_to_redact = []

        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                line_text = "".join(span["text"] for span in line["spans"]).strip()
                if not line_text:
                    continue

                should_redact = False
                line_norm = _normalize_for_clustering(line_text)

                for ptype, pattern, sig_words, pattern_zone in patterns_to_redact:
                    if ptype == "PAGE_NUMBER":
                        if _PAGE_NUMBER_RE.match(line_text):
                            # M2: zone enforcement — only redact if in header/footer/margin
                            if pattern_zone:
                                page_h = page.rect.height
                                page_w = page.rect.width
                                ly = line["bbox"][1]
                                lx = line["bbox"][0]
                                if ly < page_h * header_zone:
                                    line_zone = "header"
                                elif ly > page_h * footer_zone:
                                    line_zone = "footer"
                                elif lx < page_w * left_margin:
                                    line_zone = "left_margin"
                                elif lx > page_w * right_margin:
                                    line_zone = "right_margin"
                                else:
                                    line_zone = "content"
                                if line_zone == "content":
                                    continue  # M2: skip PAGE_NUMBER in content zone
                            should_redact = True
                            break
                    elif ptype == "TEXT_REPEAT":
                        # Match normalized line against normalized pattern
                        if pattern == line_norm:
                            # Zone check: only redact if line is in same zone as detected pattern
                            if pattern_zone:
                                page_h = page.rect.height
                                page_w = page.rect.width
                                ly = line["bbox"][1]
                                lx = line["bbox"][0]
                                if ly < page_h * header_zone:
                                    line_zone = "header"
                                elif ly > page_h * footer_zone:
                                    line_zone = "footer"
                                elif lx < page_w * left_margin:
                                    line_zone = "left_margin"
                                elif lx > page_w * right_margin:
                                    line_zone = "right_margin"
                                else:
                                    line_zone = "content"
                                if line_zone != pattern_zone:
                                    continue  # Skip: different zone
                            should_redact = True
                            break

                if should_redact:
                    # Redact the entire line bbox
                    bbox = pymupdf.Rect(line["bbox"])
                    rects_to_redact.append(bbox)
                    spans_redacted += 1

        if rects_to_redact:
            for r in rects_to_redact:
                page.add_redact_annot(r, fill=None)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
            modified_pages += 1

    return modified_pages, spans_redacted


def _strip_digital_signatures(doc, report):
    """
    Remove digital signature widgets by deleting them from the page's /Annots
    array and the document's /AcroForm/Fields. Also nulls out the signature
    value objects to reclaim space.

    This does NOT use redaction (white rectangles) — it removes the annotation
    objects entirely, preserving any page content that was under the signature.

    Returns: number of signatures removed
    """
    sig_candidates = [c for c in report.get("removal_candidates", [])
                      if c["category"] == "DIGITAL_SIG"]
    if not sig_candidates:
        return 0

    import re as _re

    # Collect widget xrefs and their value/appearance xrefs
    sig_widget_xrefs = set()
    sig_value_xrefs = set()

    for page_idx in set(c["page_idx"] for c in sig_candidates):
        page = doc[page_idx]
        for w in (page.widgets() or []):
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                sig_widget_xrefs.add(w.xref)
                # V (signature value) — can be ~36KB of PKCS7 data
                v = doc.xref_get_key(w.xref, "V")
                if v[0] == 'xref':
                    sig_value_xrefs.add(int(_re.search(r'(\d+)\s+\d+\s+R', v[1]).group(1)))

    count = len(sig_widget_xrefs)

    # 1. Remove widgets from page /Annots arrays
    for page_idx in set(c["page_idx"] for c in sig_candidates):
        page = doc[page_idx]
        annots_key = doc.xref_get_key(page.xref, "Annots")
        if annots_key[0] == 'array':
            arr = [int(x) for x in _re.findall(r'(\d+)\s+\d+\s+R', annots_key[1])]
            remaining = [x for x in arr if x not in sig_widget_xrefs]
            doc.xref_set_key(page.xref, "Annots",
                            "null" if not remaining else
                            "[" + " ".join(f"{x} 0 R" for x in remaining) + "]")

    # 2. Remove from /AcroForm/Fields in document catalog
    catalog_xref = doc.pdf_catalog()
    af_key = doc.xref_get_key(catalog_xref, "AcroForm")
    if af_key[0] == 'dict':
        new_af = _re.sub(r'/Fields\s*\[[^\]]*\]', '/Fields[]', af_key[1])
        doc.xref_set_key(catalog_xref, "AcroForm", new_af)

    # 3. Null out widget objects (AP, V, etc.)
    for xref in sig_widget_xrefs:
        try:
            doc.xref_set_key(xref, "AP", "null")
            doc.xref_set_key(xref, "V", "null")
        except Exception:
            pass

    # 4. Shrink signature value objects (empty Contents, minimal ByteRange)
    for xref in sig_value_xrefs:
        try:
            doc.xref_set_key(xref, "Contents", "( )")
            doc.xref_set_key(xref, "ByteRange", "[0 0 0 0]")
        except Exception:
            pass

    return count


def _strip_recurring_drawings(doc, report):
    """
    Remove vector drawings that repeat at the same position on many pages.

    Uses content stream surgery: replaces the drawing operators (re, l, m, etc.)
    within the flagged rect area with whitespace/nothing.

    Returns: (modified_pages, drawings_removed)
    """
    candidates = [c for c in report.get("removal_candidates", [])
                  if c["category"] == "DRAWING_REPEAT"]
    if not candidates:
        return 0, 0

    # ── Safety filter: skip drawings that cover too much of the page ──
    # Redaction removes ALL content in the rect area — using it on a large
    # frame would wipe out text.  Only remove small decorations (margins,
    # headers, footers, circles, separator lines).  Large frames are still
    # reported as detected but not stripped.
    MAX_AREA_RATIO = 0.30  # skip if drawing covers >30% of page area

    # Build a lookup: page_idx → list of rects to remove
    page_removals = defaultdict(list)
    skipped = []
    for c in candidates:
        q_rect = c.get("quantized_rect")
        if q_rect:
            dw = q_rect[2] - q_rect[0]
            dh = q_rect[3] - q_rect[1]
            draw_area = dw * dh
        else:
            draw_area = 0

        for page_idx, rect in c.get("occurrences", []):
            if draw_area > 0:
                page = doc[page_idx]
                page_area = page.rect.width * page.rect.height
                if page_area > 0 and draw_area / page_area > MAX_AREA_RATIO:
                    if c not in skipped:
                        skipped.append(c)
                    continue
            page_removals[page_idx].append(pymupdf.Rect(rect))

    modified_pages = 0
    drawings_removed = 0

    for page_idx, rects in page_removals.items():
        page = doc[page_idx]

        # Redact drawing areas with white fill to cover vector drawings.
        # fill=None does NOT affect vector drawings (only text spans).
        # True removal requires content-stream surgery (pikepdf roadmap).
        for r in rects:
            page.add_redact_annot(r, fill=(1, 1, 1))

        # Apply redactions but DON'T affect images
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        modified_pages += 1
        drawings_removed += len(rects)

    return modified_pages, drawings_removed


def strip_pdf(pdf_path, output_path, report, categories=None):
    """
    Remove flagged images and text watermarks from PDF.

    WARNING: Modifying PDFs (especially linearized ones) causes file size growth
    (10-150% depending on content). For the pipeline use case, prefer using
    the audit report with extract_clean_text() instead — it produces markdown
    with noise filtered out, without modifying the PDF.

    Image removal: content stream surgery (removes /Name Do operators)
    Text removal: redaction with white fill

    Args:
        pdf_path: Input PDF path
        output_path: Output PDF path
        report: Dict from analyze_pdf_images()
        categories: Categories to strip. Default: all

    Returns:
        dict with stats
    """
    if categories is None:
        categories = {'HIGH_FREQ', 'DUPLICATE', 'PERCEPTUAL_DUP', 'TINY', 'SIGNATURE', 'DIGITAL_SIG', 'TEXT_REPEAT', 'PAGE_NUMBER', 'DRAWING_REPEAT'}
    elif isinstance(categories, str):
        categories = set(categories.split(','))

    img_categories = categories - {'TEXT_REPEAT', 'PAGE_NUMBER', 'DIGITAL_SIG', 'DRAWING_REPEAT'}
    txt_categories = categories & {'TEXT_REPEAT', 'PAGE_NUMBER'}
    sig_categories = categories & {'DIGITAL_SIG'}
    draw_categories = categories & {'DRAWING_REPEAT'}

    # Open source PDF
    doc = pymupdf.open(pdf_path)

    # Strip images
    img_pages, img_xrefs = 0, 0
    if img_categories:
        img_pages, img_xrefs = _strip_images_content_stream(doc, report, img_categories)

    # Strip digital signatures (redact visual + neutralize widget)
    sig_count = 0
    if sig_categories:
        sig_count = _strip_digital_signatures(doc, report)

    # Strip text
    txt_pages, txt_spans = 0, 0
    if txt_categories:
        txt_pages, txt_spans = _strip_text_redaction(doc, report, txt_categories)

    # Strip recurring drawings (vector decorations)
    draw_pages, draw_removed = 0, 0
    if draw_categories:
        draw_pages, draw_removed = _strip_recurring_drawings(doc, report)

    # Full re-optimization: garbage collect + deflate.
    # Current MuPDF/PyMuPDF builds no longer support linearisation on save,
    # so keep optimization but disable linear=True to avoid FzErrorArgument.
    doc.save(output_path,
             garbage=4,       # max GC: remove unused objects, merge duplicate streams
             deflate=True,    # re-compress all streams
             clean=True,      # sanitize content streams
             linear=False)    # linearisation unsupported by current MuPDF
    doc.close()

    orig_size = os.path.getsize(pdf_path)
    out_size = os.path.getsize(output_path)

    return {
        "image_pages_modified": img_pages,
        "image_operators_removed": img_xrefs,
        "digital_signatures_removed": sig_count,
        "text_pages_modified": txt_pages,
        "text_spans_redacted": txt_spans,
        "drawing_pages_modified": draw_pages,
        "drawings_removed": draw_removed,
        "original_size": orig_size,
        "output_size": out_size,
        "overhead_bytes": out_size - orig_size,
        "overhead_pct": f"{(out_size - orig_size) * 100 / orig_size:.2f}%"
    }


# ─── Clean text extraction ───────────────────────────────────────────────────

def extract_clean_text(pdf_path, report, output_path=None, skip_images=True,
                       skip_page_numbers=True, skip_text_repeat=True):
    """
    Extract text from PDF, filtering out flagged noise (headers, footers, logos).

    This is the recommended approach for the tender pipeline: produces clean
    markdown without modifying the source PDF at all.

    Filtering:
    - Page numbering lines ("Página N de M") are excluded
    - TEXT_REPEAT lines (header/footer text) are excluded
    - Content from pages with only flagged images is still extracted
    - All other text is preserved verbatim

    Args:
        pdf_path: Input PDF path
        report: Dict from analyze_pdf_images()
        output_path: If provided, save markdown to this file
        skip_images: If True, note pages with only flagged images
        skip_page_numbers: If True, exclude page numbering lines
        skip_text_repeat: If True, exclude repeated header/footer text

    Returns:
        str: Clean markdown text
    """
    # Build filter sets from report
    page_number_norms = set()
    page_number_zones = {}  # norm → zone ("header"/"footer"/etc) — M2
    text_repeat_norms = set()
    text_repeat_zones = {}  # norm → zone ("header"/"footer")

    for c in report.get("removal_candidates", []):
        if c["category"] == "PAGE_NUMBER" and skip_page_numbers:
            page_number_norms.add(c["text"])
            if "zone" in c:
                page_number_zones[c["text"]] = c["zone"]
        elif c["category"] == "TEXT_REPEAT" and skip_text_repeat:
            text_repeat_norms.add(c["text"])
            if "zone" in c:
                text_repeat_zones[c["text"]] = c["zone"]

    doc = pymupdf.open(pdf_path)
    pages_text = []

    # Read zone thresholds from report (M1: consistent with detection)
    zones = report.get("summary", {}).get("zones", {})
    _header_zone = zones.get("header_zone", 0.12)
    _footer_zone = zones.get("footer_zone", 0.88)
    _left_margin = zones.get("left_margin", 0.08)
    _right_margin = zones.get("right_margin", 0.90)

    for i in range(len(doc)):
        page = doc[i]
        h = page.rect.height
        page_w = page.rect.width
        try:
            blocks = page.get_text("dict")["blocks"]
        except:
            continue

        page_lines = []
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                line_text = "".join(span["text"] for span in line["spans"]).strip()
                if not line_text:
                    continue

                # Filter: page numbers (M2: only skip if in header/footer/margin zone)
                if skip_page_numbers and _PAGE_NUMBER_RE.match(line_text):
                    # M2: check if this page number pattern has a detected zone
                    pn_norm = _normalize_for_clustering(line_text)
                    if pn_norm in page_number_zones:
                        detected_zone = page_number_zones[pn_norm]
                        ly = line["bbox"][1]
                        lx = line["bbox"][0]
                        if ly < h * _header_zone:
                            line_zone = "header"
                        elif ly > h * _footer_zone:
                            line_zone = "footer"
                        elif lx < page_w * _left_margin:
                            line_zone = "left_margin"
                        elif lx > page_w * _right_margin:
                            line_zone = "right_margin"
                        else:
                            line_zone = "content"
                        if line_zone == "content":
                            pass  # Don't skip — page number in content zone
                        else:
                            continue
                    else:
                        continue  # No zone info — skip (backward compatible)

                # Filter: repeated header/footer/margin text (normalized match + zone check)
                if skip_text_repeat:
                    norm = _normalize_for_clustering(line_text)
                    if norm in text_repeat_norms:
                        # Verify zone matches: only remove if line is in same zone as detected pattern
                        if norm in text_repeat_zones:
                            detected_zone = text_repeat_zones[norm]
                            ly = line["bbox"][1]
                            lx = line["bbox"][0]
                            if ly < h * _header_zone:
                                line_zone = "header"
                            elif ly > h * _footer_zone:
                                line_zone = "footer"
                            elif lx < page_w * _left_margin:
                                line_zone = "left_margin"
                            elif lx > page_w * _right_margin:
                                line_zone = "right_margin"
                            else:
                                line_zone = "content"
                            if line_zone == detected_zone:
                                continue
                            # Line is in a different zone — don't remove
                        else:
                            continue

                page_lines.append(line_text)

        if page_lines:
            page_text = "\n".join(page_lines)
            pages_text.append(f"## Página {i + 1}\n\n{page_text}")

    doc.close()

    result = f"# {report['summary']['file']}\n\n"
    result += f"Extraído con filtrado de ruido: {len(page_number_norms)} patrones de paginación, "
    result += f"{len(text_repeat_norms)} patrones de header/footer removidos.\n\n"
    result += "---\n\n"
    result += "\n\n---\n\n".join(pages_text)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


# ─── Console output ──────────────────────────────────────────────────────────

def print_report(report):
    """Print human-readable audit summary."""
    s = report["summary"]
    print(f"\n{'='*65}")
    print(f"  PDF Audit: {s['file']}")
    print(f"{'='*65}")
    print(f"  Size: {s['file_size_human']} | Pages: {s['total_pages']}")
    print(f"  Image xrefs: {s['total_image_xrefs']} | Page-refs: {s['total_image_refs']}")
    print(f"  Unique image hashes: {s['unique_image_hashes']}")
    print(f"  Images flagged: {s['images_flagged']} | Text flagged: {s['text_flagged']}")
    print(f"  Content images remaining: {s['content_images_remaining']}")

    cats = s.get('categories', {})
    if cats:
        print(f"  Categories: {', '.join(f'{k}={v}' for k,v in cats.items())}")

    if not report["removal_candidates"]:
        print("\n  No removal candidates found. PDF is clean.")
        return

    # Separate image, text and drawing candidates for display
    img_cands = [c for c in report["removal_candidates"]
                 if c["category"] not in ("TEXT_REPEAT", "PAGE_NUMBER", "DRAWING_REPEAT")]
    txt_cands = [c for c in report["removal_candidates"]
                 if c["category"] in ("TEXT_REPEAT", "PAGE_NUMBER")]
    draw_cands = [c for c in report["removal_candidates"]
                  if c["category"] == "DRAWING_REPEAT"]

    if img_cands:
        print(f"\n  {'Category':<12} {'Size':<12} {'Pages':>5}  Description")
        print(f"  {'─'*12} {'─'*12} {'─'*5}  {'─'*40}")
        for c in sorted(img_cands, key=lambda x: (-x["page_count"], x["category"])):
            sz = f"{c['width']}x{c['height']}"
            print(f"  {c['category']:<12} {sz:<12} {c['page_count']:>5}  {c['label']}")

    if txt_cands:
        print(f"\n  {'Category':<14} Description")
        print(f"  {'─'*14} {'─'*48}")
        for c in txt_cands:
            print(f"  {c['category']:<14} {c['label']}")

    if draw_cands:
        print(f"\n  {'Category':<16} {'Pages':>5}  Description")
        print(f"  {'─'*16} {'─'*5}  {'─'*45}")
        for c in sorted(draw_cands, key=lambda x: -x["page_count"]):
            print(f"  {c['category']:<16} {c['page_count']:>5}  {c['label']}")


def print_strip_result(result):
    """Print strip operation results."""
    print(f"\n  {'='*50}")
    print(f"  Strip Results")
    print(f"  {'='*50}")
    if result.get("image_operators_removed"):
        print(f"  Images: {result['image_operators_removed']} Do operators removed from {result['image_pages_modified']} pages")
    if result.get("digital_signatures_removed"):
        print(f"  Digital signatures: {result['digital_signatures_removed']} redacted")
    if result.get("text_spans_redacted"):
        print(f"  Text:   {result['text_spans_redacted']} spans redacted on {result['text_pages_modified']} pages")
    if result.get("drawings_removed"):
        print(f"  Drawings: {result['drawings_removed']} recurring drawings removed from {result['drawing_pages_modified']} pages")
    if not result.get("image_operators_removed") and not result.get("text_spans_redacted") and not result.get("digital_signatures_removed") and not result.get("drawings_removed"):
        print(f"  Nothing to strip.")
    print(f"  Original: {result['original_size']:,} bytes")
    print(f"  Output:   {result['output_size']:,} bytes")
    print(f"  Overhead: {result['overhead_bytes']:,} bytes ({result['overhead_pct']})")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Audit and strip duplicate images and text watermarks from PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.pdf                              # Audit only
  %(prog)s input.pdf --strip -o input_clean.pdf   # Audit + strip all
  %(prog)s input.pdf --strip --no-text            # Strip images only (skip text)
  %(prog)s input.pdf --strip --text-only          # Strip text only (skip images)
  %(prog)s input.pdf --strip --categories HIGH_FREQ,TEXT_REPEAT
  %(prog)s input.pdf --min-freq 3 --tiny-max 15   # Custom thresholds
        """
    )
    parser.add_argument("pdf", help="Input PDF file")
    parser.add_argument("--strip", action="store_true", help="Strip flagged images and text (modifies PDF)")
    parser.add_argument("--extract", action="store_true", help="Extract clean text as markdown (recommended for pipeline)")
    parser.add_argument("--output", "-o", help="Output path (PDF for --strip, .md for --extract)")
    parser.add_argument("--report", "-r", help="Save JSON report to this path")
    parser.add_argument("--min-freq", type=int, default=5, help="Min pages for HIGH_FREQ (default 5)")
    parser.add_argument("--tiny-max", type=int, default=20, help="Max px for TINY (default 20)")
    parser.add_argument("--text-threshold", type=float, default=0.3,
                        help="Min frequency for TEXT_REPEAT (default 0.3 = 30%%)")
    parser.add_argument("--text-min-pages", type=int, default=5,
                        help="Min pages for TEXT_REPEAT (default 5)")
    parser.add_argument("--header-zone", type=float, default=0.12,
                        help="Top %% of page to scan for headers (default 0.12)")
    parser.add_argument("--footer-zone", type=float, default=0.88,
                        help="Bottom %% of page to scan for footers (default 0.88)")
    parser.add_argument("--left-margin", type=float, default=0.08,
                        help="Left %% of page for margin zone (default 0.08)")
    parser.add_argument("--right-margin", type=float, default=0.90,
                        help="Right %% of page for margin zone (default 0.90)")
    parser.add_argument("--categories", help="Comma-separated categories to strip (default: all)")
    parser.add_argument("--text-only", action="store_true", help="Only remove text watermarks")
    parser.add_argument("--page-analysis", action="store_true",
                        help="Produce {stem}_page_analysis.json with per-page content metrics (size, text, images, drawings)")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"Error: {args.pdf} not found", file=sys.stderr)
        sys.exit(1)

    if args.strip and not args.output:
        base, ext = os.path.splitext(args.pdf)
        args.output = f"{base}_clean{ext}"

    # Audit
    report = analyze_pdf_images(args.pdf, min_freq=args.min_freq,
                                 tiny_max_px=args.tiny_max,
                                 text_threshold=args.text_threshold,
                                 text_min_pages=args.text_min_pages,
                                 header_zone=args.header_zone,
                                 footer_zone=args.footer_zone,
                                 left_margin=args.left_margin,
                                 right_margin=args.right_margin)
    print_report(report)

    # Save report
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n  Report saved to: {args.report}")

    # Save page analysis separately if requested (or if present in report)
    if report.get("page_analysis") and (args.page_analysis or args.report):
        base, ext = os.path.splitext(args.pdf)
        pa_path = f"{base}_page_analysis.json"
        with open(pa_path, "w") as f:
            json.dump(report["page_analysis"], f, indent=2, default=str)
        pa = report["page_analysis"]
        n_candidates = sum(1 for p in pa["pages"] if p.get("plan_candidate_signals"))
        print(f"\n  Page analysis saved to: {pa_path}")
        print(f"  Pages with plan candidate signals: {n_candidates}/{pa['summary']['page_count']}")
        if pa['summary']['signal_types']:
            print(f"  Signal types: {', '.join(pa['summary']['signal_types'])}")

    # Extract clean text (recommended for pipeline)
    if args.extract:
        out_path = args.output or args.pdf.rsplit(".", 1)[0] + "_clean.md"
        md = extract_clean_text(args.pdf, report, output_path=out_path)
        total_chars = len(md)
        lines = md.count("\n")
        print(f"\n  Extracted: {total_chars:,} chars, {lines:,} lines → {out_path}")

    # Strip PDF (modifies file, causes size growth on linearized PDFs)
    if args.strip:
        if args.categories:
            cats = set(args.categories.split(','))
        elif args.no_text:
            cats = {'HIGH_FREQ', 'DUPLICATE', 'PERCEPTUAL_DUP', 'TINY', 'SIGNATURE', 'DIGITAL_SIG'}
        elif args.text_only:
            cats = {'TEXT_REPEAT', 'PAGE_NUMBER'}
        else:
            cats = None  # all

        result = strip_pdf(args.pdf, args.output, report, categories=cats)
        print_strip_result(result)
        print(f"\n  Output: {args.output}")


if __name__ == "__main__":
    main()
