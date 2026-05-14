# Roadmap: PDF Decorative Element Cleaning

> Status: Detection mature. Stripping has known limitations. This document outlines the path forward.

## Current State (v5 — commit e43040b)

### What works well
- **Detection**: 9 categories (HIGH_FREQ, DUPLICATE, PERCEPTUAL_DUP, TINY, SIGNATURE, DIGITAL_SIG, TEXT_REPEAT, PAGE_NUMBER, DRAWING_REPEAT)
- **Sibling key fusion**: Handles sub-pixel jitter (Pass 1: spatial ≤2pt) and odd/even page mirroring (Pass 2: same size + visual properties, position-independent)
- **Safe area filter**: Drawings covering >30% of page area are detected but not stripped, preventing catastrophic text loss
- **Text redaction with fill=None**: Removes text operators without inserting white rectangles
- **Zone-based text detection**: 5 zones (header, footer, left_margin, right_margin, content) — only eliminates text that repeats in the same zone across ≥50% of pages

### Known limitations

1. **Large decorative frames cannot be removed** — redaction is spatial and would destroy content inside the frame. Detected but skipped.

2. **Red circle around page numbers** — detected and stripped via redaction, which works because it's small (22×22pt). But the same technique fails for large frames.

3. **Footnote numbers sometimes caught** — TEXT_REPEAT in footer zone can flag legitimate footnote markers ("1", "2", "N") that appear in the same footer position on many pages. Possible mitigation: minimum text length filter, or context-aware check (is the span isolated vs. grouped with other decorative elements).

4. **Redaction is a blunt instrument for drawings** — even with fill=None and PDF_REDACT_IMAGE_NONE, it removes ALL content within the rect, not just the specific path operators. Works for small elements (circles, lines) but is inherently unsafe for large ones.

---

## Recommended Architecture: PyMuPDF + pikepdf Hybrid

### Why pikepdf?

PyMuPDF excels at detection (`get_drawings()`) but offers no API to delete individual shapes. Its only removal mechanism is redaction (spatial area wipe). Confirmed by maintainer discussions.

pikepdf (Python wrapper for qpdf/C++) provides:
- `parse_content_stream(page)` — tokenizes the content stream into (operands, operator) pairs
- `unparse_content_stream(operations)` — reconstructs the content stream
- Token-level filtering — surgical removal of specific operator sequences
- qpdf's robust PDF handling under the hood (linearization, cross-reference tables, etc.)

### The approach: content stream surgery

In PDF, a "drawing" is not an object — it's a sequence of path construction operators followed by a paint operator, embedded inline in the page's content stream:

```
Path construction:  m  l  c  v  y  h  re
Paint operators:    S  s  f  F  f*  B  B*  b  b*  n
```

A rounded rectangle is typically: `m → l → c → l → c → l → c → l → c → h → S` (move, lines, Bézier curves for corners, close, stroke).

**Algorithm:**
1. PyMuPDF detects the target drawing (position, size, color, page frequency)
2. pikepdf parses the content stream
3. Group operators into "path segments": accumulate from path construction start until a paint operator
4. Calculate bounding box of each path segment from the operand coordinates
5. Match against the detected drawing's bbox (with tolerance for quantization)
6. If match → drop that segment; otherwise keep it
7. `unparse_content_stream()` the filtered operations back to the page
8. Save with pikepdf

### Edge cases to handle

- **Graphics state save/restore** (`q`/`Q`): A path might be wrapped in `q ... Q` for color/width setup. Must strip the whole block.
- **Multiple content streams**: Some pages have their content split across multiple streams (Form XObjects). Need to process all of them.
- **Inline images** (`BI ... ID ... EI`): Can appear between path operators — must not break the parser.
- **Color space matching**: The detection uses PyMuPDF's normalized RGB. The content stream might use CMYK, grayscale, or indexed color spaces. Need to resolve color in the stream's native space.
- **Transparency groups**: Drawing might be inside a `/Group` or `/SMask` context.

### Why not other libraries?

| Library | Verdict |
|---------|---------|
| **pypdf** | Has `ContentStream` but less robust with real-world PDFs. Pikepdf/qpdf is battle-tested. |
| **borb** | Higher-level API, not suited for stream-level surgery. |
| **qpdf CLI** | Powerful but C++/CLI. pikepdf is the Python path to the same engine. |
| **Apryse/PDFTron** | Commercial. `ElementReader`/`ElementWriter` API is exactly what's needed, but heavy SDK + license. |
| **iText** | Commercial (AGPL for OSS). Powerful but licensing complexity. |

### Implementation sketch

```python
import pikepdf

def strip_drawing_from_page(page_pike, target_bbox, tolerance=2.0):
    """
    Remove a specific drawing from a pikepdf Page by filtering its content stream.
    
    Args:
        page_pike: pikepdf Page object
        target_bbox: (x0, y0, x1, y1) from PyMuPDF detection
        tolerance: pt tolerance for bbox matching
    """
    PATH_CONSTRUCT = {'m', 'l', 'c', 'v', 'y', 'h', 're'}
    PAINT = {'S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'n'}
    
    operations = pikepdf.parse_content_stream(page_pike)
    filtered = []
    current_path = []  # accumulates path construction + operands
    in_graphics_state = False
    graphics_state_prefix = []
    
    for operands, operator in operations:
        op = str(operator)
        
        if op == 'q':
            in_graphics_state = True
            graphics_state_prefix.append((operands, operator))
            continue
        
        if op in PATH_CONSTRUCT:
            current_path.append((operands, operator))
            continue
        
        if op in PAINT:
            current_path.append((operands, operator))
            path_bbox = calculate_bbox(current_path)
            if path_bbox and bbox_matches(path_bbox, target_bbox, tolerance):
                # Drop this path + any enclosing q/Q
                current_path = []
                graphics_state_prefix = []
                in_graphics_state = False
                continue
            else:
                # Keep it
                if in_graphics_state:
                    filtered.extend(graphics_state_prefix)
                filtered.extend(current_path)
                current_path = []
                graphics_state_prefix = []
                in_graphics_state = False
                continue
        
        if op == 'Q' and in_graphics_state:
            # Close graphics state block — flush any pending path
            if current_path:
                filtered.extend(graphics_state_prefix)
                filtered.extend(current_path)
                current_path = []
            filtered.append((operands, operator))
            graphics_state_prefix = []
            in_graphics_state = False
            continue
        
        # Non-path operator — flush any accumulated path, then emit
        if current_path:
            if in_graphics_state:
                filtered.extend(graphics_state_prefix)
            filtered.extend(current_path)
            current_path = []
            graphics_state_prefix = []
            in_graphics_state = False
        filtered.append((operands, operator))
    
    page_pike.contents_add(
        pikepdf.unparse_content_stream(filtered),
        prepend=False
    )
```

---

## Priority TODO

1. **pikepdf content stream surgery for large frames** — the main gap. Medium effort, high impact.
2. **Footnote number protection** — filter TEXT_REPEAT candidates shorter than 3 chars in footer zone, or check if the span is part of a footnote context (preceded by superscript, followed by text on same line).
3. **Text redaction → content stream surgery** — same pikepdf approach for text removal, eliminating dependency on redaction entirely. Would also handle edge cases where redaction affects nearby content.
4. **Batch benchmark** — run against a corpus of 10+ Peruvian government PDFs to tune thresholds (zone boundaries, area ratios, fusion tolerance).

## Dependencies to add

```
pikepdf>=8.0  # MIT license, wraps qpdf C++ library
```

Already in Debian repos as `python3-pikepdf` or via pip.
