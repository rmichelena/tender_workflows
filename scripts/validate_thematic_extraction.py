#!/usr/bin/env python3
"""Validate Paso 2A thematic extraction JSON.

Performs JSON Schema validation plus workflow-specific checks that are hard to
express in schema: line ranges, evidence quote length, chunk coverage shape, and
source bounds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument(
        "--schema",
        type=Path,
        default=Path("instrucciones/D_portafolio/schemas/thematic_extraction.schema.json"),
    )
    ap.add_argument("--source-md", type=Path)
    ap.add_argument("--chunk-plan", type=Path)
    ap.add_argument("--max-evidence-chars", type=int, default=400)
    args = ap.parse_args()

    data = load(args.json_path)
    schema = load(args.schema)
    errors: list[str] = []

    for err in Draft202012Validator(schema).iter_errors(data):
        errors.append(f"schema {list(err.path)}: {err.message}")

    source_md = args.source_md or Path(data.get("source_md_path", ""))
    chunk_plan = args.chunk_plan or Path(data.get("chunk_plan_path", ""))
    if not source_md.is_absolute() and not source_md.exists():
        # Leave unresolved relative paths as a warning-like error; callers should pass absolute paths.
        errors.append(f"source markdown not found: {source_md}")
        total_lines = None
    elif source_md.exists():
        total_lines = count_lines(source_md)
    else:
        total_lines = None

    if chunk_plan and Path(chunk_plan).exists():
        cp = load(Path(chunk_plan))
        missing = cp.get("coverage", {}).get("missing_ranges", [])
        if missing:
            errors.append(f"chunk plan has missing ranges: {missing}")
        expected_ranges = {(c["line_start"], c["line_end"]) for c in cp.get("chunks", [])}
        reported_ranges = {(r["line_start"], r["line_end"]) for r in data.get("coverage", {}).get("line_ranges_read", [])}
        if expected_ranges and not expected_ranges.issubset(reported_ranges):
            missing_reported = sorted(expected_ranges - reported_ranges)[:10]
            errors.append(f"coverage.line_ranges_read does not include all chunk ranges; missing {missing_reported}")
    else:
        errors.append(f"chunk plan not found: {chunk_plan}")

    ids = set()
    for i, e in enumerate(data.get("entries", []), 1):
        prefix = f"entry[{i}] {e.get('entry_id', '<no id>')}:"
        eid = e.get("entry_id")
        if eid in ids:
            errors.append(f"{prefix} duplicate entry_id")
        ids.add(eid)
        start, end = e.get("source_line_start"), e.get("source_line_end")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            errors.append(f"{prefix} invalid line range {start}-{end}")
        if total_lines and isinstance(end, int) and end > total_lines:
            errors.append(f"{prefix} line_end {end} > source total {total_lines}")
        evidence = e.get("evidence_excerpt", "")
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append(f"{prefix} missing evidence_excerpt")
        elif len(evidence) > args.max_evidence_chars:
            errors.append(f"{prefix} evidence_excerpt length {len(evidence)} > {args.max_evidence_chars}")

    result = {
        "json_path": str(args.json_path),
        "entries": len(data.get("entries", [])),
        "errors": errors,
        "ok": not errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
