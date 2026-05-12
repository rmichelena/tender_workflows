#!/usr/bin/env python3
"""
MarkItDown — Extractor de texto PDF/DOCX a Markdown.

Usa la librería markitdown de Microsoft para convertir documentos a markdown.
Es el extractor más rápido y simple, pero NO hace OCR — solo extrae texto
embebido. Para documentos escaneados o con diagramas, usar DocAI.

Salida:
  - {nombre}_markitdown.md   — Markdown con texto extraído
  - {nombre}_markitdown.json — JSON con metadata + contenido

Uso:
  python3 markitdown_extract.py <input_file> [output_dir]

Dependencias:
  pip install markitdown
"""

import sys, os, time, json, re

sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")


def fix_ligatures(text):
    """Corrige ligaturas tipográficas comunes (fi, fl, ff, ffi, ffl)."""
    return (text.replace("\ufb01", "fi").replace("\ufb02", "fl")
                .replace("\ufb00", "ff").replace("\ufb03", "ffi")
                .replace("\ufb04", "ffl"))


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 markitdown_extract.py <input_file> [output_dir]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_file)
    os.makedirs(output_dir, exist_ok=True)
    base = re.sub(r'[^a-zA-Z0-9._-]', '_', os.path.basename(input_file).rsplit(".", 1)[0])

    print(f"Processing: {input_file}")
    t0 = time.time()

    from markitdown import MarkItDown
    md_converter = MarkItDown()
    result = md_converter.convert(input_file)
    md = fix_ligatures(result.text_content)

    elapsed = time.time() - t0

    md_path = os.path.join(output_dir, f"{base}_markitdown.md")
    json_path = os.path.join(output_dir, f"{base}_markitdown.json")

    with open(md_path, "w") as f:
        f.write(md)
    with open(json_path, "w") as f:
        json.dump({
            "source": input_file, "extractor": "markitdown",
            "elapsed_seconds": round(elapsed, 1),
            "markdown_length": len(md),
            "content": md,
        }, f, indent=2, ensure_ascii=False)

    print(f"Done: {elapsed:.1f}s | {len(md):,} chars | ~{len(md.split()):,} words")
    print(f"Saved: {md_path}, {json_path}")


if __name__ == "__main__":
    main()
