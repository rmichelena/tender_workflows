#!/usr/bin/env python3
"""Build deterministic section-aware reading chunks from a document_index JSON.

Chunks target ~500 lines and prefer section/numeral boundaries from Paso 1.5.
They are consumed by Paso 2A thematic readers so subagents do not invent their
own chunking strategy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def normalize_sections(index: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for s in index.get("sections", []):
        try:
            start = int(s["line_start"])
            end = int(s["line_end"])
        except Exception:
            continue
        if start <= 0 or end < start:
            continue
        sections.append({
            "section_id": str(s.get("section_id") or f"sec_{start}_{end}"),
            "title": str(s.get("title") or s.get("heading_text_raw") or ""),
            "section_kind": str(s.get("section_kind") or "section"),
            "level": int(s.get("level") or 1),
            "line_start": start,
            "line_end": end,
            "parent_section_id": s.get("parent_section_id"),
        })
    sections.sort(key=lambda x: (x["line_start"], x["line_end"], x["level"]))
    return sections


def leafish_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer usable structural units while avoiding giant parent chapters when children cover them."""
    if not sections:
        return []
    result = []
    for s in sections:
        # Keep all non-root units. Root chapters often overlap with their children;
        # if a level-1/large section has children inside, skip it as a chunk atom.
        has_child = any(
            c is not s
            and c["line_start"] >= s["line_start"]
            and c["line_end"] <= s["line_end"]
            and c["level"] > s["level"]
            for c in sections
        )
        span = s["line_end"] - s["line_start"] + 1
        if has_child and (s["level"] <= 1 or span > 250):
            continue
        result.append(s)
    # Remove exact duplicate ranges keeping the most specific / highest level.
    by_range: dict[tuple[int, int], dict[str, Any]] = {}
    for s in result:
        key = (s["line_start"], s["line_end"])
        old = by_range.get(key)
        if old is None or s["level"] > old["level"]:
            by_range[key] = s
    return sorted(by_range.values(), key=lambda x: (x["line_start"], x["line_end"]))


def build_chunks(atoms: list[dict[str, Any]], total_lines: int, target: int, hard_max: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        chunks.append(make_chunk(len(chunks) + 1, cur))
        cur = []

    for atom in atoms:
        span = atom["line_end"] - atom["line_start"] + 1
        if span > hard_max:
            flush()
            # Large section: split mechanically but mark that we had to do it.
            start = atom["line_start"]
            part = 1
            while start <= atom["line_end"]:
                end = min(atom["line_end"], start + target - 1)
                chunks.append({
                    "chunk_id": f"chunk_{len(chunks)+1:03d}",
                    "line_start": start,
                    "line_end": end,
                    "section_ids": [atom["section_id"]],
                    "section_titles": [atom["title"]],
                    "split_from_section_id": atom["section_id"],
                    "split_part": part,
                    "notes": "Large indexed section split mechanically because no finer section boundary was available. Use neighboring context if needed.",
                })
                if end == atom["line_end"]:
                    break
                start = max(end - 49, start + 1)  # 50-line overlap for forced split only
                part += 1
            continue

        if not cur:
            cur.append(atom)
            continue
        proposed_start = min(cur[0]["line_start"], atom["line_start"])
        proposed_end = max(cur[-1]["line_end"], atom["line_end"])
        proposed_span = proposed_end - proposed_start + 1
        current_span = cur[-1]["line_end"] - cur[0]["line_start"] + 1
        if current_span >= int(target * 0.75) and proposed_span > target:
            flush()
            cur.append(atom)
        elif proposed_span > hard_max:
            flush()
            cur.append(atom)
        else:
            cur.append(atom)
    flush()

    # If no atoms covered the doc, fall back to whole-doc chunks.
    if not chunks and total_lines:
        start = 1
        while start <= total_lines:
            end = min(total_lines, start + target - 1)
            chunks.append({
                "chunk_id": f"chunk_{len(chunks)+1:03d}",
                "line_start": start,
                "line_end": end,
                "section_ids": [],
                "section_titles": [],
                "split_from_section_id": None,
                "split_part": None,
                "notes": "Fallback fixed-line chunk; no usable section index atoms found.",
            })
            start = end + 1
    return chunks


def make_chunk(n: int, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "chunk_id": f"chunk_{n:03d}",
        "line_start": atoms[0]["line_start"],
        "line_end": max(a["line_end"] for a in atoms),
        "section_ids": [a["section_id"] for a in atoms],
        "section_titles": [a["title"] for a in atoms],
        "split_from_section_id": None,
        "split_part": None,
        "notes": "Section-boundary chunk derived from Paso 1.5 index.",
    }


def close_small_coverage_gaps(chunks: list[dict[str, Any]], total_lines: int) -> list[dict[str, Any]]:
    """Extend neighboring chunks so every source line is assigned.

    Step 1.5 indices can have small line gaps between structural sections. The
    reader still needs full-document coverage, so deterministic chunks absorb
    those gaps rather than leaving agents to invent extra windows.
    """
    if not chunks:
        return chunks
    chunks = sorted(chunks, key=lambda c: (c["line_start"], c["line_end"]))
    if chunks[0]["line_start"] > 1:
        chunks[0]["line_start"] = 1
        chunks[0]["notes"] += " Leading uncovered lines absorbed."
    prev = chunks[0]
    for cur in chunks[1:]:
        if cur["line_start"] > prev["line_end"] + 1:
            prev["line_end"] = cur["line_start"] - 1
            prev["notes"] += " Following uncovered gap absorbed."
        elif cur["line_start"] <= prev["line_end"]:
            # Preserve order but avoid accidental overlap unless the chunk is a
            # forced split, where overlap is intentional.
            if not cur.get("split_from_section_id"):
                cur["line_start"] = prev["line_end"] + 1
        prev = cur
    if total_lines and chunks[-1]["line_end"] < total_lines:
        chunks[-1]["line_end"] = total_lines
        chunks[-1]["notes"] += " Trailing uncovered lines absorbed."
    return chunks


def validate_coverage(chunks: list[dict[str, Any]], total_lines: int) -> dict[str, Any]:
    covered = set()
    for c in chunks:
        covered.update(range(c["line_start"], c["line_end"] + 1))
    missing = []
    if total_lines:
        start = None
        prev = None
        for i in range(1, total_lines + 1):
            if i not in covered:
                if start is None:
                    start = prev = i
                elif i == prev + 1:
                    prev = i
                else:
                    missing.append({"line_start": start, "line_end": prev})
                    start = prev = i
        if start is not None:
            missing.append({"line_start": start, "line_end": prev})
    return {"total_lines": total_lines, "covered_lines": len(covered), "missing_ranges": missing}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True, type=Path)
    ap.add_argument("--source-md", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--target-lines", type=int, default=500)
    ap.add_argument("--hard-max-lines", type=int, default=750)
    args = ap.parse_args()

    index = load_json(args.index)
    total_lines = sum(1 for _ in args.source_md.open(encoding="utf-8"))
    stats_total = int(index.get("document_stats", {}).get("total_lines") or total_lines)
    total_lines = max(total_lines, stats_total)
    sections = normalize_sections(index)
    atoms = leafish_sections(sections)
    chunks = build_chunks(atoms, total_lines, args.target_lines, args.hard_max_lines)
    chunks = close_small_coverage_gaps(chunks, total_lines)
    out = {
        "schema_version": "0.1",
        "document_id": index.get("doc_id"),
        "source_md_path": str(args.source_md),
        "document_index_path": str(args.index),
        "target_chunk_lines": args.target_lines,
        "hard_max_chunk_lines": args.hard_max_lines,
        "chunking_strategy": "section_boundary_from_step_1_5_index",
        "chunks": chunks,
        "coverage": validate_coverage(chunks, total_lines),
        "notes": "Chunks are deterministic reading units for Paso 2A thematic readers. Prefer these over agent-created line windows.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "chunks": len(chunks), "coverage": out["coverage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
