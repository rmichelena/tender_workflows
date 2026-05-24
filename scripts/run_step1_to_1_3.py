#!/usr/bin/env python3
"""Generic deterministic runner for tender_procurement Gate 0 through Step 1.3.

Scope boundary:
- This runner performs deterministic file/tool steps only.
- It does NOT perform visual LLM classification for Step 1.2b.
- If plan/diagram candidates exist and no validated visual analysis JSON exists,
  it writes step_1_planos_candidates_pending.json, runs Modal Docling on the
  PDFs already ready, and exits with code 23 for the pending subset only.
- Once visual analysis JSONs are supplied by the OpenClaw orchestrator/subagents,
  rerun this script to build pre-OCR PDFs and continue to Modal Docling.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv/bin/python"
sys.path.insert(0, str(REPO / "scripts/extractors"))

STEP_DIR_NAMES = [
    "step_1_xlsx_split",
    "step_1_normalizados",
    "step_1_pdfs",
    "step_1_pdfs_clean",
    "step_1_planos",
    "step_1_pdfs_preocr",
]
STEP_FILE_NAMES = [
    "step_1_triage_inventory.json",
    "step_1_planos_candidates_pending.json",
    "step_1_3_outputs.json",
]


def run(cmd: list[Any], *, cwd: Path = REPO, timeout: int | None = None) -> tuple[subprocess.CompletedProcess[str], float]:
    print("\n$ " + " ".join(str(c) for c in cmd), flush=True)
    t0 = time.time()
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    dt = time.time() - t0
    if p.stdout:
        print(p.stdout, end="")
    if p.stderr:
        print(p.stderr, end="", file=sys.stderr)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}) after {dt:.1f}s: {' '.join(str(c) for c in cmd)}")
    return p, dt


def append_log(logs: Path, text: str) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "decision_log.md").open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")


def clear_step_outputs(project: Path) -> None:
    art = project / "artifacts"
    logs = project / "logs"
    for name in STEP_DIR_NAMES:
        p = art / name
        if p.exists():
            shutil.rmtree(p)
    for name in STEP_FILE_NAMES:
        p = art / name
        if p.exists():
            p.unlink()
    # This runner owns the step decision log. Keep other logs intact.
    decision_log = logs / "decision_log.md"
    if decision_log.exists():
        decision_log.unlink()


def dirs(project: Path) -> dict[str, Path]:
    art = project / "artifacts"
    return {
        "art": art,
        "logs": project / "logs",
        "inputs": project / "inputs",
        "xlsx_split": art / "step_1_xlsx_split",
        "normal": art / "step_1_normalizados",
        "pdfs": art / "step_1_pdfs",
        "clean": art / "step_1_pdfs_clean",
        "planos": art / "step_1_planos",
        "preocr": art / "step_1_pdfs_preocr",
    }


def ensure_dirs(d: dict[str, Path]) -> None:
    for k, p in d.items():
        if k != "inputs":
            p.mkdir(parents=True, exist_ok=True)


def valid_file(path: Path, min_size: int = 1) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size >= min_size


def valid_pdf(path: Path, min_size: int = 100) -> bool:
    if not valid_file(path, min_size):
        return False
    try:
        import fitz

        with fitz.open(path) as doc:
            return doc.page_count > 0
    except Exception:
        return False


def valid_json_file(path: Path, min_size: int = 10, *, required_keys: tuple[str, ...] = ()) -> bool:
    if not valid_file(path, min_size):
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return all(key in data for key in required_keys)


def valid_markdown(path: Path, min_size: int = 100) -> bool:
    if not valid_file(path, min_size):
        return False
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(text)


def write_step_1_3_outputs(
    d: dict[str, Path],
    entries: list[dict[str, Any]],
    *,
    pending_planos: list[dict[str, Any]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "markdown_outputs": [e["path"] for e in entries],
        "entries": entries,
    }
    if pending_planos:
        payload["pending_planos"] = pending_planos
        payload["partial"] = True
    (d["art"] / "step_1_3_outputs.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def inventory(project: Path, d: dict[str, Path]) -> tuple[list[Path], list[Path], list[Path]]:
    if not d["inputs"].is_dir():
        raise SystemExit(f"Missing inputs directory: {d['inputs']}")
    files = sorted([p for p in d["inputs"].iterdir() if p.is_file()])
    docx: list[Path] = []
    pdfs: list[Path] = []
    xlsx: list[Path] = []
    other: list[Path] = []
    inv = []
    for p in files:
        ext = p.suffix.lower()
        rec = {"path": str(p), "name": p.name, "ext": ext}
        if ext == ".docx":
            rec["branch"] = "PDF via DOCX→PDF"
            docx.append(p)
        elif ext == ".pdf":
            rec["branch"] = "PDF original"
            pdfs.append(p)
        elif ext in (".xlsx", ".xlsm"):
            rec["branch"] = "XLSX native"
            xlsx.append(p)
        else:
            rec["branch"] = "UNKNOWN"
            other.append(p)
        inv.append(rec)
    (d["art"] / "step_1_triage_inventory.json").write_text(
        json.dumps({"project": str(project), "inputs": inv}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if other:
        raise SystemExit(f"Unsupported input extensions: {[p.name for p in other]}")
    append_log(
        d["logs"],
        f"## Gate 0 / Paso 1.0 — {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- Proyecto: `{project}`\n"
        f"- Inputs: {len(files)} archivos ({len(docx)} DOCX, {len(pdfs)} PDF, {len(xlsx)} XLSX/XLSM).\n"
        f"- Alcance runner determinístico: hasta Paso 1.3; se detiene ante candidatos visuales sin análisis LLM.\n"
        f"- Output inventario: `artifacts/step_1_triage_inventory.json`",
    )
    return docx, pdfs, xlsx


def process_xlsx(xlsx: list[Path], d: dict[str, Path], overwrite: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for xp in xlsx:
        t0 = time.time()
        manifest_path = d["xlsx_split"] / f"{xp.stem}_split_manifest.json"
        if overwrite or not valid_json_file(manifest_path, required_keys=("sheets",)):
            run([PY, "scripts/extractors/xlsx_split.py", xp, "--output-dir", d["xlsx_split"]], timeout=300)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        converted = 0
        for sheet in manifest["sheets"]:
            sp = Path(sheet["output_path"])
            analysis_path = sp.with_suffix("").with_name(sp.stem + "_analysis.json")
            out_md = d["normal"] / (sp.stem + ".md")
            if overwrite or not valid_json_file(analysis_path):
                run([PY, "scripts/extractors/xlsx_sheet_analyze.py", sp, "--output-json", analysis_path], timeout=300)
            if overwrite or not valid_markdown(out_md, min_size=50):
                run([PY, "scripts/extractors/xlsx_convert.py", sp, "--analysis", analysis_path, "--output", out_md], timeout=300)
            if not valid_markdown(out_md, min_size=50):
                raise RuntimeError(f"XLSX convert no produjo markdown válido: {out_md}")
            entries.append({
                "path": str(out_md),
                "source_path": str(xp),
                "source_type": "xlsx",
                "sheet_name": sheet["original_name"],
                "extractor": "xlsx_convert",
            })
            converted += 1
        append_log(d["logs"], f"## Paso 1.0.b — XLSX normalizado\n- Input: `{xp}`\n- Manifest: `{manifest_path}`\n- Hojas procesadas: {converted}\n- Duración: {time.time()-t0:.1f}s")
    return entries


def process_pdf_inputs(docx: list[Path], pdfs: list[Path], d: dict[str, Path], overwrite: bool) -> None:
    planned: dict[str, Path] = {}
    for dp in docx:
        out = d["pdfs"] / f"{dp.stem}__from_docx.pdf"
        if out.name in planned:
            raise RuntimeError(f"Colisión de nombre PDF planificado: {out.name}")
        planned[out.name] = dp
    for pp in pdfs:
        out = d["pdfs"] / pp.name
        if out.name in planned:
            other = planned[out.name]
            raise RuntimeError(
                f"Colisión de nombre PDF: {out.name} proviene de {other.name} y {pp.name}. "
                "Renombra uno de los inputs."
            )
        planned[out.name] = pp

    for dp in docx:
        t0 = time.time()
        out = d["pdfs"] / f"{dp.stem}__from_docx.pdf"
        if overwrite or not valid_pdf(out):
            run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", d["pdfs"], dp], timeout=300)
            # LibreOffice names output {stem}.pdf; rename to collision-safe name.
            lo_out = d["pdfs"] / f"{dp.stem}.pdf"
            if lo_out.exists() and lo_out != out:
                if out.exists():
                    out.unlink()
                lo_out.rename(out)
        if not valid_pdf(out):
            raise RuntimeError(f"LibreOffice no produjo PDF válido para {dp}")
        append_log(d["logs"], f"## Paso 1.1 — DOCX→PDF\n- Input: `{dp}`\n- Output: `{out}`\n- Duración: {time.time()-t0:.1f}s")
    for pp in pdfs:
        out = d["pdfs"] / pp.name
        if overwrite or not valid_pdf(out):
            shutil.copy2(pp, out)
        if not valid_pdf(out):
            raise RuntimeError(f"PDF passthrough inválido para {pp}")
        append_log(d["logs"], f"## Paso 1.1 — PDF passthrough\n- Input: `{pp}`\n- Output: `{out}`")


def clean_pdfs(d: dict[str, Path], overwrite: bool) -> list[Path]:
    clean_pdfs: list[Path] = []
    for pdf in sorted(d["pdfs"].glob("*.pdf")):
        t0 = time.time()
        stem = pdf.stem
        clean_pdf = d["clean"] / f"{stem}_clean.pdf"
        report = d["clean"] / f"{stem}_clean_report.json"
        page_analysis = d["clean"] / f"{stem}_clean_page_analysis.json"
        if overwrite or not (
            valid_pdf(clean_pdf)
            and valid_json_file(report)
            and valid_json_file(page_analysis, required_keys=("pages",))
        ):
            run([
                PY,
                "scripts/pdf_image_audit.py",
                pdf,
                "--strip",
                "--output",
                clean_pdf,
                "--report",
                report,
                "--page-analysis-output",
                page_analysis,
            ], timeout=900)
        if not valid_pdf(clean_pdf):
            raise RuntimeError(f"Cleaning no produjo PDF válido: {clean_pdf}")
        if not valid_json_file(page_analysis, required_keys=("pages",)):
            raise RuntimeError(f"Cleaning no produjo page-analysis: {page_analysis}")
        clean_pdfs.append(clean_pdf)
        append_log(d["logs"], f"## Paso 1.2 — PDF clean + page-analysis\n- Input: `{pdf}`\n- Output: `{clean_pdf}`\n- Report: `{report}`\n- Page analysis: `{page_analysis}`\n- Duración: {time.time()-t0:.1f}s")
    return clean_pdfs


def plan_analysis_paths(planos: Path, stem: str) -> list[Path]:
    return [
        planos / f"planos_analysis_{stem}.json",
        # Backwards-compatible read only; runner no longer creates this name.
        planos / f"planos_extraidos_{stem}.json",
    ]


def audit_and_build_plan_pages(
    clean_pdfs: list[Path], d: dict[str, Path], overwrite: bool
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Audit/build plan pages per PDF.

    Returns (pending_visual_analysis, clean_pdfs_ready_for_docling).
    """
    pending: list[dict[str, Any]] = []
    ready_for_docling: list[Path] = []
    for clean_pdf in clean_pdfs:
        t0 = time.time()
        stem = clean_pdf.stem.removesuffix("_clean")
        page_analysis = d["clean"] / f"{stem}_clean_page_analysis.json"
        audit_path = d["planos"] / f"{stem}_page_size_audit.json"
        if overwrite or not valid_json_file(audit_path, required_keys=("candidates",)):
            run([
                PY,
                "scripts/pdf_plan_pages.py",
                "audit",
                clean_pdf,
                "--output-dir",
                d["planos"],
                "--stem",
                stem,
                "--render-dpi",
                "150",
                "--page-analysis",
                page_analysis,
            ], timeout=900)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        candidates = audit.get("candidates", [])
        cand_count = len(candidates)
        analysis_json = next((p for p in plan_analysis_paths(d["planos"], stem) if p.exists()), None)
        if cand_count and analysis_json is None:
            pending.append({
                "stem": stem,
                "clean_pdf": str(clean_pdf),
                "audit_path": str(audit_path),
                "candidate_count": cand_count,
                "candidate_images": [c.get("rendered_image") for c in candidates],
                "required_analysis_json": str(d["planos"] / f"planos_analysis_{stem}.json"),
            })
        else:
            if analysis_json is not None:
                # Build only when visual analysis exists. If all pages are leave_for_ocr,
                # pdf_plan_pages.py now returns null outputs and creates no placeholders.
                run([
                    PY,
                    "scripts/pdf_plan_pages.py",
                    "build",
                    clean_pdf,
                    "--output-dir",
                    d["planos"],
                    "--preocr-dir",
                    d["preocr"],
                    "--stem",
                    stem,
                    "--analysis-json",
                    analysis_json,
                ], timeout=900)
            ready_for_docling.append(clean_pdf)
        append_log(d["logs"], f"## Paso 1.2b — Planos/diagramas audit\n- Input: `{clean_pdf}`\n- Audit: `{audit_path}`\n- Candidatos visuales: {cand_count}\n- Analysis JSON: `{analysis_json}`\n- Docling: {'pendiente análisis visual' if cand_count and analysis_json is None else 'listo'}\n- Duración: {time.time()-t0:.1f}s")

    if pending:
        marker = d["art"] / "step_1_planos_candidates_pending.json"
        marker.write_text(json.dumps(pending, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        append_log(
            d["logs"],
            f"## [PARCIAL] Paso 1.2b — análisis visual requerido\n"
            f"- Documentos con candidatos pendientes: {len(pending)}\n"
            f"- Documentos listos para Docling: {len(ready_for_docling)}\n"
            f"- Marker: `{marker}`\n"
            f"- Acción requerida: lanzar subagente visual OpenClaw/Gemini y escribir "
            f"`planos_analysis_{{stem}}.json` validado; luego rerun.",
        )
        print(f"VISUAL_CANDIDATES_PENDING {marker}")
    return pending, ready_for_docling


def modal_docling(clean_pdfs: list[Path], d: dict[str, Path], overwrite: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for clean_pdf in clean_pdfs:
        t0 = time.time()
        stem = clean_pdf.stem.removesuffix("_clean")
        preocr = d["preocr"] / f"{stem}_preocr.pdf"
        src = preocr if valid_pdf(preocr) else clean_pdf
        expected = d["normal"] / f"{stem}_modal_docling.md"
        if overwrite or not valid_markdown(expected):
            run([
                PY,
                "scripts/extractors/modal_docling_extract.py",
                src,
                expected,
                "--max-wait",
                "1800",
            ], timeout=2100)
        if not valid_markdown(expected):
            raise RuntimeError(f"Modal Docling produjo output ausente/pequeño: {expected}")
        entries.append({
            "path": str(expected),
            "source_path": str(clean_pdf),
            "source_type": "pdf",
            "sheet_name": None,
            "extractor": "modal_docling",
            "docling_input": str(src),
        })
        append_log(d["logs"], f"## Paso 1.3 — Modal Docling PDF→Markdown\n- Input: `{src}`\n- Output: `{expected}`\n- Duración: {time.time()-t0:.1f}s")
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description="Run deterministic tender_procurement steps Gate 0 through 1.3 for any project folder.")
    ap.add_argument("--project", required=True, type=Path, help="Project folder containing inputs/, artifacts/, logs/.")
    ap.add_argument("--overwrite", action="store_true", help="Delete Step 1 generated artifacts/log and reprocess from scratch.")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    if args.overwrite:
        clear_step_outputs(project)
    d = dirs(project)
    ensure_dirs(d)

    docx, pdfs, xlsx = inventory(project, d)
    md_entries: list[dict[str, Any]] = []
    md_entries.extend(process_xlsx(xlsx, d, args.overwrite))
    process_pdf_inputs(docx, pdfs, d, args.overwrite)
    clean = clean_pdfs(d, args.overwrite)
    pending_planos, docling_pdfs = audit_and_build_plan_pages(clean, d, args.overwrite)
    md_entries.extend(modal_docling(docling_pdfs, d, args.overwrite))
    write_step_1_3_outputs(d, md_entries, pending_planos=pending_planos or None)
    if pending_planos:
        return 23
    print("STEP_1_TO_1_3_DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
