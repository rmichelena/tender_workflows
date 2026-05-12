#!/usr/bin/env python3
"""
Batch runner — ejecuta múltiples extractores sobre múltiples documentos.

Itera sobre todos los PDF de un directorio y ejecuta los extractores
seleccionados, generando un reporte de comparación.

Uso:
  python3 batch_runner.py --input-dir <dir> --output-dir <dir> [--extractors docai_batch,markitdown]

Extractores disponibles:
  - markitdown    — Rápido, sin OCR, texto embebido solo
  - docai_online  — Google DocAI modo online/chunked (sin GCS)
  - docai_batch   — Google DocAI modo batch con GCS (RECOMENDADO)

Ejemplo:
  python3 batch_runner.py --input-dir ./pdfs --output-dir ./results --extractors docai_batch,markitdown

Dependencias:
  Ver cada extractor individual.
"""

import sys, os, time, json, glob, argparse, subprocess

sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")

EXTRACTORS = {
    "markitdown": {
        "script": os.path.join(os.path.dirname(__file__), "markitdown_extract.py"),
        "suffix": "markitdown",
    },
    "docai_online": {
        "script": os.path.join(os.path.dirname(__file__), "docai_online.py"),
        "suffix": "docai",
    },
    "docai_batch": {
        "script": os.path.join(os.path.dirname(__file__), "docai_batch_gcs.py"),
        "suffix": "docai_batch",
    },
}


def main():
    parser = argparse.ArgumentParser(description="Batch extraction runner")
    parser.add_argument("--input-dir", required=True, help="Directorio con PDFs")
    parser.add_argument("--output-dir", required=True, help="Directorio de salida")
    parser.add_argument("--extractors", default="markitdown,docai_batch",
                        help="Extractores separados por coma (default: markitdown,docai_batch)")
    parser.add_argument("--recursive", action="store_true", help="Buscar PDFs recursivamente")
    args = parser.parse_args()

    selected = args.extractors.split(",")
    for ext in selected:
        if ext not in EXTRACTORS:
            print(f"ERROR: extractor '{ext}' no reconocido. Disponibles: {list(EXTRACTORS.keys())}")
            sys.exit(1)

    # Find PDFs
    pattern = os.path.join(args.input_dir, "**", "*.pdf") if args.recursive else os.path.join(args.input_dir, "*.pdf")
    pdfs = sorted(glob.glob(pattern, recursive=args.recursive))
    if not pdfs:
        print(f"No se encontraron PDFs en {args.input_dir}")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDFs")
    print(f"Extractors: {selected}")
    print("=" * 60)

    results = []

    for pdf_path in pdfs:
        name = os.path.basename(pdf_path)
        print(f"\n>>> {name}")

        for ext_name in selected:
            ext = EXTRACTORS[ext_name]
            script = ext["script"]
            if not os.path.exists(script):
                print(f"  SKIP {ext_name}: script no encontrado ({script})")
                continue

            cmd = [sys.executable, script, pdf_path, args.output_dir]
            print(f"  [{ext_name}] Running...", end=" ", flush=True)

            t0 = time.time()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                elapsed = time.time() - t0

                if r.returncode == 0:
                    # Find output file
                    base = os.path.basename(pdf_path).rsplit(".", 1)[0]
                    base_safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
                    suffix = ext["suffix"]
                    md_path = os.path.join(args.output_dir, f"{base_safe}_{suffix}.md")
                    md_size = os.path.getsize(md_path) if os.path.exists(md_path) else 0
                    print(f"OK ({elapsed:.1f}s, {md_size:,} chars)")
                    results.append({"file": name, "extractor": ext_name, "status": "ok",
                                    "elapsed": round(elapsed, 1), "chars": md_size})
                else:
                    print(f"FAIL (exit {r.returncode})")
                    print(f"    {r.stderr[:200]}")
                    results.append({"file": name, "extractor": ext_name, "status": "error",
                                    "error": r.stderr[:200]})
            except subprocess.TimeoutExpired:
                print(f"TIMEOUT (30min)")
                results.append({"file": name, "extractor": ext_name, "status": "timeout"})
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"file": name, "extractor": ext_name, "status": "error", "error": str(e)})

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        status = r["status"]
        if status == "ok":
            print(f"  {r['file']:<35} {r['extractor']:<15} {r['elapsed']:>6.1f}s  {r['chars']:>10,} chars")
        else:
            print(f"  {r['file']:<35} {r['extractor']:<15} {status.upper()}")

    # Save summary
    summary_path = os.path.join(args.output_dir, "batch_summary.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {summary_path}")


if __name__ == "__main__":
    main()
