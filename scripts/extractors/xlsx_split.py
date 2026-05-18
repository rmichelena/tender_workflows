#!/usr/bin/env python3
"""
xlsx_split.py — Paso 1.0.b.1 del workflow.

Divide un archivo XLSX/XLSM multi-pestaña en varios archivos XLSX single-tab,
preservando metadata del origen para trazabilidad downstream.

Por qué este paso:
    Excel multi-pestaña no es procesable directamente porque cada hoja puede
    tener una representación óptima distinta (markdown table, HTML table, o texto).
    Separar a single-tab permite decidir y convertir cada hoja independientemente.

Uso:
    python3 xlsx_split.py INPUT.xlsx --output-dir DIR

Outputs (en DIR):
    {stem}_sheet{NN}_{slug}.xlsx           — un XLSX por hoja
    {stem}_split_manifest.json             — metadata: hojas, índices, paths, conteos

Convención de naming:
    NN: índice 1-based zero-padded (01, 02, ...).
    slug: nombre de hoja saneado a [a-z0-9_], cortado a 40 chars.

Determinístico. No LLM. Idempotente (sobrescribe outputs).

Dependencias: openpyxl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from openpyxl import load_workbook
except ImportError:
    sys.stderr.write(
        "openpyxl no instalado. Instalar con:\n"
        "  pip install --break-system-packages openpyxl\n"
    )
    sys.exit(1)


def slugify(name: str, max_len: int = 40) -> str:
    """Sanitizar nombre de hoja para usar como parte de un filename."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    if not s:
        s = "sheet"
    return s[:max_len]


def split_xlsx(input_path: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    # data_only=False preserva fórmulas. data_only=True devuelve valores cacheados.
    # Para el workflow downstream queremos VALORES (no fórmulas), así que data_only=True.
    # PERO: si el XLSX no fue abierto por Excel desde la última edición, los valores
    # cacheados pueden estar vacíos. Estrategia: cargar primero con data_only=True;
    # si una hoja tiene celdas todas None pero la versión con data_only=False sí tiene
    # fórmulas, advertir.
    wb_values = load_workbook(filename=str(input_path), data_only=True)
    wb_formulas = load_workbook(filename=str(input_path), data_only=False)

    stem = input_path.stem
    sheets_meta = []

    for idx, sheet_name in enumerate(wb_values.sheetnames, start=1):
        ws_values = wb_values[sheet_name]
        ws_formulas = wb_formulas[sheet_name]

        # Detectar si la hoja parece vacía
        non_empty_cells = 0
        for row in ws_values.iter_rows(values_only=True):
            for cell in row:
                if cell is not None and cell != "":
                    non_empty_cells += 1

        # Detectar si las fórmulas tienen valores cacheados perdidos
        has_formulas = False
        has_cached_values_for_formulas = False
        for row in ws_formulas.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    has_formulas = True
                    # Mirar valor cacheado en la versión data_only
                    v = ws_values.cell(cell.row, cell.column).value
                    if v is not None and v != "":
                        has_cached_values_for_formulas = True

        warnings = []
        if has_formulas and not has_cached_values_for_formulas and non_empty_cells == 0:
            warnings.append(
                "sheet_appears_empty_but_has_formulas_without_cached_values"
            )

        slug = slugify(sheet_name)
        out_name = f"{stem}_sheet{idx:02d}_{slug}.xlsx"
        out_path = output_dir / out_name

        # Crear un workbook con solo esta hoja. Forma robusta: cargar el original,
        # eliminar todas las demás, guardar como output.
        # Hacemos copia desde wb_formulas (preserva fórmulas Y formato y merged cells).
        from openpyxl import load_workbook as _lw  # reimport para nueva instancia

        wb_single = _lw(filename=str(input_path), data_only=False)
        for other_name in list(wb_single.sheetnames):
            if other_name != sheet_name:
                del wb_single[other_name]
        wb_single.save(str(out_path))

        sheets_meta.append({
            "index": idx,
            "original_name": sheet_name,
            "slug": slug,
            "output_path": str(out_path),
            "non_empty_cells": non_empty_cells,
            "has_formulas": has_formulas,
            "has_cached_values_for_formulas": has_cached_values_for_formulas,
            "warnings": warnings,
        })

    manifest = {
        "source": str(input_path),
        "stem": stem,
        "total_sheets": len(sheets_meta),
        "output_dir": str(output_dir),
        "sheets": sheets_meta,
    }

    manifest_path = output_dir / f"{stem}_split_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("input", help="Archivo XLSX/XLSM de entrada (multi-pestaña).")
    parser.add_argument(
        "--output-dir", required=True,
        help="Directorio donde escribir los XLSX single-tab + manifest.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.stderr.write(f"Input no existe: {input_path}\n")
        return 2
    if input_path.suffix.lower() not in (".xlsx", ".xlsm"):
        sys.stderr.write(
            f"Formato no soportado: {input_path.suffix}. Esperado .xlsx o .xlsm\n"
        )
        return 2

    output_dir = Path(args.output_dir)
    manifest = split_xlsx(input_path, output_dir)

    print(f"OK: {manifest['stem']} → {manifest['total_sheets']} hojas en {output_dir}")
    for s in manifest["sheets"]:
        warn_str = f" ⚠ {','.join(s['warnings'])}" if s["warnings"] else ""
        print(
            f"  [{s['index']:02d}] {s['original_name']!r:40s} "
            f"({s['non_empty_cells']} celdas){warn_str}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
