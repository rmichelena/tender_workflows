"""Puente hacia tender_procurement (Paso 1 determinístico + eje 0 Gemini)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import AppConfig, TenderProcurementConfig
from ..tender_repo import resolve_tender_repo_root

logger = logging.getLogger(__name__)

EXIT_VISUAL_PENDING = 23


def tender_project_root(proc_dir: Path) -> Path:
    """Carpeta tipo `proyecto/` esperada por run_step1_to_1_3.py."""
    return proc_dir / "tender_project"


def prepare_tender_project(proc_dir: Path, documents_dir: Path) -> Path:
    """Copia documentos descargados a tender_project/inputs/."""
    project = tender_project_root(proc_dir)
    inputs = project / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (project / "artifacts").mkdir(exist_ok=True)
    (project / "logs").mkdir(exist_ok=True)

    for f in documents_dir.iterdir():
        if not f.is_file() or f.name == "manifest.json":
            continue
        dest = inputs / f.name
        if dest.exists():
            dest.unlink()
        shutil.copy2(f, dest)

    meta = {
        "source": "seace-monitor",
        "nomenclatura": proc_dir.name,
        "files": sorted(p.name for p in inputs.iterdir() if p.is_file()),
    }
    (project / "project_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return project


def _repo_python(repo: Path) -> Path:
    venv_py = repo / ".venv" / "bin" / "python"
    return venv_py if venv_py.exists() else Path(sys.executable)


def run_step1_deterministic(repo: Path, project: Path, overwrite: bool = True) -> int:
    script = repo / "scripts" / "run_step1_to_1_3.py"
    if not script.exists():
        raise FileNotFoundError(f"No existe {script}")

    cmd = [
        str(_repo_python(repo)),
        str(script),
        "--project",
        str(project),
    ]
    if overwrite:
        cmd.append("--overwrite")

    logger.info("Ejecutando tender_procurement Paso 1.0–1.3: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    if proc.stdout:
        logger.info(proc.stdout[-4000:])
    if proc.stderr:
        logger.warning(proc.stderr[-2000:])
    return proc.returncode


def resolve_planos_pending(project: Path, mode: str) -> bool:
    """
    Si run_step1 salió con 23, genera JSON de análisis visual según mode.
    Retorna True si se puede reintentar el runner.
    """
    art = project / "artifacts"
    marker = art / "step_1_planos_candidates_pending.json"
    if not marker.exists():
        return False

    pending = json.loads(marker.read_text(encoding="utf-8"))
    if mode == "stop":
        raise RuntimeError(
            f"Candidatos visuales pendientes (planos). Revisar {marker} y completar "
            "planos_analysis_{{stem}}.json con Gemini antes de continuar."
        )

    if mode != "auto_leave":
        raise ValueError(f"planos_mode no soportado aún: {mode}")

    planos_dir = art / "step_1_planos"
    planos_dir.mkdir(parents=True, exist_ok=True)

    for item in pending:
        stem = item["stem"]
        audit_path = Path(item["audit_path"])
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        pages = []
        for cand in audit.get("candidates", []):
            pages.append(
                {
                    "page": cand.get("page", 0),
                    "action": "leave_for_ocr",
                    "identifier_or_title": None,
                    "visual_type": "skipped_auto",
                    "summary": "Auto leave_for_ocr (seace-monitor planos_mode=auto_leave)",
                    "technical_observations": [],
                    "visible_text_or_codes": [],
                    "limitations": ["Sin análisis Gemini 1.2b"],
                    "confidence": "low",
                }
            )
        out = planos_dir / f"planos_analysis_{stem}.json"
        payload = {
            "schema_version": "0.3",
            "source_pdf": item.get("clean_pdf", ""),
            "model": "seace-monitor/auto_leave",
            "pages": pages,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("planos_analysis auto_leave: %s (%s páginas)", out.name, len(pages))

    marker.unlink()
    return True


def run_axis0_gemini(
    repo: Path,
    project: Path,
    tender_cfg: TenderProcurementConfig,
) -> Path | None:
    """Paso 1.3b: lectura libre con Gemini sobre markdowns normalizados."""
    if not tender_cfg.run_axis0:
        return None

    api_key = os.environ.get(tender_cfg.gemini_api_key_env, "")
    if not api_key:
        logger.warning(
            "Sin %s — se omite eje 0 Gemini", tender_cfg.gemini_api_key_env
        )
        return None

    try:
        from google import genai  # type: ignore
    except ImportError:
        logger.warning("Instala google-genai para eje 0: pip install google-genai")
        return None

    normal = project / "artifacts" / "step_1_normalizados"
    if not normal.is_dir():
        raise RuntimeError(f"No existe carpeta normalizada: {normal}")

    md_files = sorted(normal.glob("*.md"))
    if not md_files:
        raise RuntimeError("Sin archivos .md en step_1_normalizados")

    prompt_path = repo / "instrucciones" / "prompts" / "prompt_axis0_free_reader.md"
    system = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else (
        "Extrae alcance, requisitos, cronograma y datos comerciales de las bases."
    )

    parts = [
        f"Documentos en expediente ({len(md_files)} archivos markdown):\n",
    ]
    for md in md_files[:12]:
        text = md.read_text(encoding="utf-8", errors="replace")
        parts.append(f"\n\n### ARCHIVO: {md.name}\n\n{text[:80_000]}")

    user = "".join(parts)
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=tender_cfg.gemini_model,
        contents=[system, user],
    )
    summary_text = getattr(response, "text", None) or str(response)

    out_dir = project / "artifacts" / "step_1_axis0_preindex"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "axis0_go_no_go_summary.md"
    summary_path.write_text(summary_text, encoding="utf-8")
    logger.info("Eje 0 guardado en %s", summary_path)
    return summary_path


def parse_axis0_summary(md_path: Path) -> dict[str, str]:
    """Extrae campos principales del markdown de eje 0 para la BD."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    sections = _split_md_sections(text)
    return {
        "alcance": _pick_section(sections, "alcance", "objeto", "contratación", "contratacion"),
        "incluye": _pick_section(sections, "incluye", "bienes", "equipos", "familias"),
        "requisitos": _pick_section(
            sections, "requisitos", "postor", "experiencia", "calificación", "calificacion"
        ),
        "raw_summary": text,
    }


def _split_md_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = "_intro"
    buf: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s+", line):
            sections[current] = "\n".join(buf).strip()
            current = line.lstrip("#").strip().lower()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def _pick_section(sections: dict[str, str], *keywords: str) -> str:
    for title, body in sections.items():
        if any(k in title for k in keywords):
            return body[:8000]
    return sections.get("_intro", "")[:8000]


def run_tender_stage1(config: AppConfig, proc_dir: Path, documents_dir: Path) -> dict[str, Any]:
    """
    Pipeline completo etapa «análisis» (hasta 1.3b):
    - tender_procurement 1.0–1.3 (determinístico)
    - resolución planos (auto_leave o stop)
    - Gemini eje 0 (opcional)
    """
    tender_cfg = config.analysis.tender
    repo = tender_cfg.repo_path or resolve_tender_repo_root()
    if not repo.exists():
        raise FileNotFoundError(
            f"Raíz tender_workflows no encontrada: {repo}"
        )

    project = prepare_tender_project(proc_dir, documents_dir)
    code = run_step1_deterministic(repo, project, overwrite=True)

    if code == EXIT_VISUAL_PENDING:
        if resolve_planos_pending(project, tender_cfg.planos_mode):
            code = run_step1_deterministic(repo, project, overwrite=False)

    if code != 0:
        raise RuntimeError(
            f"tender_procurement run_step1_to_1_3 terminó con código {code}"
        )

    result: dict[str, Any] = {
        "tender_project": str(project),
        "step_1_3": json.loads(
            (project / "artifacts" / "step_1_3_outputs.json").read_text(encoding="utf-8")
        )
        if (project / "artifacts" / "step_1_3_outputs.json").exists()
        else {},
    }

    summary = run_axis0_gemini(repo, project, tender_cfg)
    if summary:
        result["axis0"] = parse_axis0_summary(summary)
        result["axis0_path"] = str(summary)

    return result
