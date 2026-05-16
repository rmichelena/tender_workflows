#!/usr/bin/env python3
"""Render a thematic-reader prompt from a common template + axis payload JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def bullet(value: Any) -> str:
    if value is None:
        return "- (no especificado)"
    if isinstance(value, list):
        if not value:
            return "- (ninguno)"
        out = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text", "")
                include = item.get("include")
                reason = item.get("reason", "")
                verdict = "INCLUIR" if include is True else "EXCLUIR" if include is False else "REVISAR"
                out.append(f"- {verdict}: {text} — {reason}")
            else:
                out.append(f"- {item}")
        return "\n".join(out)
    return str(value)


def render(template: str, payload: dict[str, Any]) -> str:
    mapping = {
        "role_frame": payload.get("role_frame", ""),
        "task_axis_definition": payload.get("task_axis_definition", ""),
        "inclusion_rules": bullet(payload.get("inclusion_rules", [])),
        "exclusion_rules": bullet(payload.get("exclusion_rules", [])),
        "trigger_phrases": bullet(payload.get("trigger_phrases", [])),
        "axis_disambiguation_examples": bullet(payload.get("axis_disambiguation_examples", [])),
        "phase_rules": bullet(payload.get("phase_rules", [])),
        "axis_specific_fields": bullet(payload.get("axis_specific_fields", [])),
    }
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", type=Path, default=Path("instrucciones/prompts/prompt_thematic_reader.template.md"))
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    template = args.template.read_text(encoding="utf-8")
    payload = load_json(args.payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(template, payload), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "axis_id": payload.get("axis_id")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
