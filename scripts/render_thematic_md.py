#!/usr/bin/env python3
"""Render Paso 2A thematic extraction JSON into human Markdown.

The JSON is canonical. Markdown is a deterministic derived artifact for review.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def esc(s: Any) -> str:
    return str(s or "").replace("\n", " ").strip()


def render(data: dict[str, Any]) -> str:
    entries = data.get("entries", [])
    phase_counts = collections.Counter(e.get("phase") for e in entries)
    mention_counts = collections.Counter(e.get("mention_type") for e in entries)
    context_counts = collections.Counter(e.get("source_context_type") for e in entries)
    conditional_counts = collections.Counter(e.get("conditional_applicability") for e in entries)

    lines: list[str] = []
    lines.append(f"# Extracción temática — {esc(data.get('document_id'))} / {esc(data.get('axis_id'))}")
    lines.append("")
    lines.append(f"- **axis:** {esc(data.get('axis_name'))}")
    lines.append(f"- **source:** `{esc(data.get('source_md_path'))}`")
    lines.append(f"- **index:** `{esc(data.get('document_index_path'))}`")
    lines.append(f"- **chunks:** `{esc(data.get('chunk_plan_path'))}`")
    lines.append(f"- **entries:** {len(entries)}")
    lines.append("")
    lines.append("## Conteos")
    lines.append("")
    lines.append(f"- Por fase: {dict(phase_counts)}")
    lines.append(f"- Por tipo de mención: {dict(mention_counts)}")
    lines.append(f"- Por contexto fuente: {dict(context_counts)}")
    lines.append(f"- Por aplicabilidad condicional: {dict(conditional_counts)}")
    lines.append("")

    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for e in entries:
        grouped[str(e.get("phase", "unclear"))].append(e)

    lines.append("## Entradas")
    for phase in sorted(grouped.keys()):
        lines.append("")
        lines.append(f"### {phase} ({len(grouped[phase])})")
        for e in grouped[phase]:
            primary = "primary" if e.get("is_primary_requirement") else "secondary"
            cond = e.get("conditional_applicability")
            lines.append("")
            lines.append(f"#### `{esc(e.get('entry_id'))}` — {esc(e.get('description'))}")
            lines.append(f"- Líneas: {e.get('source_line_start')}–{e.get('source_line_end')}")
            lines.append(f"- Tipo: `{esc(e.get('entry_type'))}` · `{esc(e.get('mention_type'))}` · `{esc(e.get('source_context_type'))}` · `{primary}` · `{cond}`")
            sp = e.get("section_path") or []
            if sp:
                lines.append(f"- Sección: {' > '.join(esc(x) for x in sp)}")
            lines.append(f"- Cita: “{esc(e.get('evidence_excerpt'))}”")
            if e.get("interpretation_notes"):
                lines.append(f"- Notas: {esc(e.get('interpretation_notes'))}")
            if e.get("dedupe_context"):
                lines.append(f"- Contexto dedupe: {esc(e.get('dedupe_context'))}")
            if e.get("cross_axis_notes"):
                lines.append(f"- Cruce ejes: {esc(e.get('cross_axis_notes'))}")

    lines.append("")
    lines.append("## Incertidumbres")
    lines.append("")
    uncertainties = data.get("uncertainties", [])
    if not uncertainties:
        lines.append("Sin incertidumbres reportadas.")
    else:
        for u in uncertainties:
            lines.append(f"- **{esc(u.get('severity'))}** líneas {u.get('line_start')}–{u.get('line_end')}: {esc(u.get('description'))}")

    lines.append("")
    lines.append("## Cobertura")
    lines.append("")
    cov = data.get("coverage", {})
    ranges = cov.get("line_ranges_read", [])
    lines.append(f"- Rangos leídos: {len(ranges)}")
    if ranges:
        lines.append("- " + ", ".join(f"{r.get('line_start')}–{r.get('line_end')}" for r in ranges))
    if cov.get("coverage_notes"):
        lines.append(f"- Notas: {esc(cov.get('coverage_notes'))}")

    sf = data.get("schema_feedback", {})
    lines.append("")
    lines.append("## Feedback schema")
    lines.append("")
    lines.append(f"- Suficiente: {sf.get('sufficient')}")
    for change in sf.get("suggested_changes", []) or []:
        lines.append(f"- Cambio sugerido: {esc(change)}")
    if sf.get("notes"):
        lines.append(f"- Notas: {esc(sf.get('notes'))}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument("output_md", type=Path)
    args = ap.parse_args()
    data = load(args.json_path)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render(data), encoding="utf-8")
    print(json.dumps({"output_md": str(args.output_md), "entries": len(data.get("entries", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
