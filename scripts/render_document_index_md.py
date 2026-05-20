#!/usr/bin/env python3
"""Render tender_procurement document_index JSON files to deterministic Markdown.

Usage:
  python scripts/render_document_index_md.py artifacts/step_1_index/foo_index.json
  python scripts/render_document_index_md.py artifacts/step_1_index/*_index.json
  python scripts/render_document_index_md.py --check artifacts/step_1_index/*_index.json

The LLM indexer owns only the canonical JSON. This script owns the human-readable
Markdown so formatting is stable and can be regenerated without re-indexing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def esc(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).replace("\n", " ").strip()
    return s.replace("|", "\\|")


def sev_rank(sev: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(sev, 9)


def render(data: dict[str, Any], source_path: Path) -> str:
    doc_id = data.get("doc_id", source_path.stem.removesuffix("_index"))
    stats = data.get("document_stats", {})
    sections = data.get("sections", [])
    tables = data.get("tables_or_forms", [])
    warnings = data.get("structural_warnings", [])
    corrections = data.get("markdown_corrections_suggested", [])
    feedback = data.get("schema_feedback", {})
    method = data.get("index_method", {})

    lines: list[str] = []
    lines.append(f"# Índice estructural — {doc_id}")
    lines.append("")
    lines.append("## Resumen")
    lines.append("")
    lines.append(f"- Fuente: `{data.get('source_md_path', '')}`")
    lines.append(f"- Líneas fuente: {stats.get('total_lines', 'n/d')}")
    lines.append(f"- Caracteres fuente: {stats.get('total_chars', 'n/d')}")
    lines.append(f"- Método: ventanas de {method.get('chunk_line_count', 200)} líneas con overlap {method.get('overlap_line_count', 50)}")
    lines.append(f"- Secciones: {len(sections)}")
    lines.append(f"- Tablas/formularios: {len(tables)}")
    lines.append(f"- Warnings estructurales: {len(warnings)}")
    lines.append(f"- Correcciones Markdown sugeridas: {len(corrections)}")
    lines.append("")

    lines.append("## Secciones")
    lines.append("")
    lines.append("| ID | Padre | Nivel | Líneas | Tipo | Contenido | Categoría | Conf. | Título | Resumen |")
    lines.append("|---|---|---:|---:|---|---|---|---|---|---|")
    for s in sections:
        indent_title = ("↳ " * max(0, int(s.get("level", 1)) - 1)) + esc(s.get("title"))
        lines.append(
            "| {id} | {parent} | {level} | {start}-{end} | {kind} | {pred} | {cat} | {conf} | {title} | {summary} |".format(
                id=esc(s.get("section_id")),
                parent=esc(s.get("parent_section_id")),
                level=esc(s.get("level")),
                start=esc(s.get("line_start")),
                end=esc(s.get("line_end")),
                kind=esc(s.get("section_kind")),
                pred=esc(s.get("predominant_content")),
                cat=esc(s.get("category_hint")),
                conf=esc(s.get("confidence")),
                title=indent_title,
                summary=esc(s.get("summary")),
            )
        )
    lines.append("")

    lines.append("## Tablas y formularios")
    lines.append("")
    if tables:
        lines.append("| ID | Sección | Líneas | Tipo | Categoría | Conf. | Título | Resumen |")
        lines.append("|---|---|---:|---|---|---|---|---|")
        for t in tables:
            lines.append(
                "| {id} | {sec} | {start}-{end} | {kind} | {cat} | {conf} | {title} | {summary} |".format(
                    id=esc(t.get("id")),
                    sec=esc(t.get("section_id")),
                    start=esc(t.get("line_start")),
                    end=esc(t.get("line_end")),
                    kind=esc(t.get("kind")),
                    cat=esc(t.get("category_hint")),
                    conf=esc(t.get("confidence")),
                    title=esc(t.get("title")),
                    summary=esc(t.get("summary")),
                )
            )
    else:
        lines.append("No se registraron tablas/formularios.")
    lines.append("")

    lines.append("## Warnings estructurales")
    lines.append("")
    if warnings:
        lines.append("| Severidad | Líneas | Sección | Tipo | Descripción |")
        lines.append("|---|---:|---|---|---|")
        for w in sorted(warnings, key=lambda x: (sev_rank(x.get("severity", "")), x.get("line_start", 0))):
            lines.append(
                "| {sev} | {start}-{end} | {sec} | {typ} | {desc} |".format(
                    sev=esc(w.get("severity")),
                    start=esc(w.get("line_start")),
                    end=esc(w.get("line_end")),
                    sec=esc(w.get("section_id")),
                    typ=esc(w.get("type")),
                    desc=esc(w.get("description")),
                )
            )
    else:
        lines.append("No se registraron warnings estructurales.")
    lines.append("")

    lines.append("## Correcciones Markdown sugeridas")
    lines.append("")
    if corrections:
        lines.append("| ID | Líneas | Sección | Tipo | Conf. | Auto | Motivo |")
        lines.append("|---|---:|---|---|---|---|---|")
        for c in corrections:
            lines.append(
                "| {id} | {start}-{end} | {sec} | {typ} | {conf} | {auto} | {reason} |".format(
                    id=esc(c.get("correction_id")),
                    start=esc(c.get("line_start")),
                    end=esc(c.get("line_end")),
                    sec=esc(c.get("section_id")),
                    typ=esc(c.get("type")),
                    conf=esc(c.get("confidence")),
                    auto="sí" if c.get("safe_auto_apply") else "no",
                    reason=esc(c.get("reason")),
                )
            )
        lines.append("")
        lines.append("### Detalle de correcciones")
        lines.append("")
        for c in corrections:
            lines.append(f"#### {esc(c.get('correction_id'))} — {esc(c.get('type'))}")
            lines.append(f"- Líneas: {esc(c.get('line_start'))}-{esc(c.get('line_end'))}")
            lines.append(f"- Safe auto-apply: {'sí' if c.get('safe_auto_apply') else 'no'}")
            lines.append(f"- Extracto original: `{esc(c.get('original_excerpt'))}`")
            lines.append(f"- Reemplazo sugerido: `{esc(c.get('suggested_replacement'))}`")
            lines.append(f"- Riesgo: {esc(c.get('risk_notes'))}")
            lines.append("")
    else:
        lines.append("No se sugirieron correcciones Markdown.")
        lines.append("")

    lines.append("## Feedback de schema")
    lines.append("")
    lines.append(f"- Suficiente: {'sí' if feedback.get('sufficient') else 'no'}")
    if feedback.get("insufficient_fields"):
        lines.append("- Campos insuficientes: " + ", ".join(esc(x) for x in feedback.get("insufficient_fields", [])))
    if feedback.get("excessive_fields"):
        lines.append("- Campos excesivos: " + ", ".join(esc(x) for x in feedback.get("excessive_fields", [])))
    if feedback.get("suggested_changes"):
        lines.append("- Cambios sugeridos:")
        for x in feedback.get("suggested_changes", []):
            lines.append(f"  - {esc(x)}")
    if feedback.get("notes"):
        lines.append(f"- Notas: {esc(feedback.get('notes'))}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_file(path: Path, check: bool = False) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = path.with_suffix(".md")
    content = render(data, path)
    if check:
        if not out.exists():
            raise SystemExit(f"Missing rendered markdown: {out}")
        current = out.read_text(encoding="utf-8")
        if current != content:
            raise SystemExit(f"Rendered markdown differs: {out}")
    else:
        out.write_text(content, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Render document_index JSON to deterministic Markdown.")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="Fail if rendered markdown is missing or stale; do not write.")
    args = ap.parse_args()
    for p in args.paths:
        out = render_file(p, check=args.check)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
