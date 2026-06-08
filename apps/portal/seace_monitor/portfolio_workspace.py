"""Preparación determinística del workspace de portafolio."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .config import AppConfig
from .db.models import Process, utcnow
from .analysis.document_prep import resolve_selected_documents
from .parser import fechas_listado_from_cronograma_json


MANIFEST_NAME = "staging_manifest.json"
SEED_PROMPT_NAME = "seed_prompt.md"
CONTEXT_NAME = "context.json"

DOCUMENT_ROLE_LABELS = {
    "bases_iniciales": "Bases iniciales",
    "aclaraciones": "Aclaraciones / respuestas",
    "bases_aclaradas": "Bases aclaradas / integradas",
    "especificaciones_tecnicas": "Especificaciones técnicas",
    "otros": "Otros",
}
_DATE_TIME_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?"
)
_LIMA_TZ = timezone(timedelta(hours=-5))


@dataclass(frozen=True)
class PortfolioWorkspaceStatus:
    prepared: bool
    portfolio_dir: Path | None
    inputs_dir: Path | None
    manifest_path: Path | None
    seed_prompt_path: Path | None
    context_path: Path | None
    selected_count: int = 0
    prepared_at: str | None = None


def portfolio_dir_for_process(process: Process) -> Path:
    if not process.data_dir:
        raise RuntimeError("Sin data_dir; descarga los documentos primero.")
    return Path(process.data_dir) / "portafolio"


def portfolio_workspace_status(process: Process) -> PortfolioWorkspaceStatus:
    if not process.data_dir:
        return PortfolioWorkspaceStatus(False, None, None, None, None, None)
    portfolio_dir = Path(process.data_dir) / "portafolio"
    manifest_path = portfolio_dir / MANIFEST_NAME
    seed_prompt_path = portfolio_dir / SEED_PROMPT_NAME
    context_path = portfolio_dir / CONTEXT_NAME
    selected_count = 0
    prepared_at = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
        selected = manifest.get("selected_documents")
        if isinstance(selected, list):
            selected_count = len(selected)
        prepared_at = str(manifest.get("prepared_at") or "") or None
    return PortfolioWorkspaceStatus(
        prepared=manifest_path.is_file() and seed_prompt_path.is_file(),
        portfolio_dir=portfolio_dir,
        inputs_dir=portfolio_dir / "inputs",
        manifest_path=manifest_path,
        seed_prompt_path=seed_prompt_path,
        context_path=context_path,
        selected_count=selected_count,
        prepared_at=prepared_at,
    )


def prepare_portfolio_workspace(
    config: AppConfig,
    process: Process,
    selected_rel_paths: list[str],
    *,
    document_roles: dict[str, str] | None = None,
    notes: str = "",
    prepared_by: str = "portal",
) -> dict:
    """Copia documentos seleccionados a portafolio/inputs y escribe seed Hermes."""
    if not process.data_dir:
        raise RuntimeError("Sin data_dir; descarga los documentos primero.")
    proc_dir = Path(process.data_dir)
    docs_dir = proc_dir / "documentos"
    selected_paths = resolve_selected_documents(docs_dir, selected_rel_paths)

    portfolio_dir = proc_dir / "portafolio"
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = portfolio_dir / "logs"
    outputs_dir = portfolio_dir / "outputs"
    artifacts_dir = portfolio_dir / "artifacts"
    logs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    inputs_dir = portfolio_dir / "inputs"
    tmp_inputs = portfolio_dir / f".inputs_tmp_{uuid4().hex}"
    tmp_inputs.mkdir(parents=True, exist_ok=False)

    selected_documents: list[dict] = []
    document_roles = document_roles or {}
    try:
        for source_path in selected_paths:
            rel = source_path.relative_to(docs_dir)
            rel_key = str(rel).replace("\\", "/")
            document_role = normalize_document_role(document_roles.get(rel_key))
            dest_path = tmp_inputs / rel
            final_dest_path = inputs_dir / rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
            selected_documents.append(
                {
                    "source_path": _rel_to_process(proc_dir, source_path),
                    "dest_path": _rel_to_process(proc_dir, final_dest_path),
                    "original_name": source_path.name,
                    "sha256": _sha256(dest_path),
                    "included": True,
                    "document_role": document_role,
                    "document_role_label": DOCUMENT_ROLE_LABELS[document_role],
                }
            )
        _replace_inputs_dir(inputs_dir, tmp_inputs, portfolio_dir)
    except Exception:
        shutil.rmtree(tmp_inputs, ignore_errors=True)
        raise

    now = utcnow().astimezone(timezone.utc).isoformat()
    scenario = infer_portfolio_scenario(process, selected_documents)
    manifest = {
        "version": "0.1",
        "process_id": process.id,
        "tenant_id": config.tenant_id,
        "source": process.source,
        "selected_documents": selected_documents,
        "uploads": [],
        "clarifications": _clarifications_from_documents(selected_documents),
        "portfolio_scenario": scenario,
        "free_reader_profile": _load_free_reader_profile(proc_dir),
        "notes": notes.strip(),
        "prepared_at": now,
        "prepared_by": prepared_by.strip() or "portal",
    }

    _backup_if_exists(portfolio_dir / MANIFEST_NAME, portfolio_dir)
    (portfolio_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    context = _build_context(config, process, proc_dir, portfolio_dir, scenario)
    (portfolio_dir / CONTEXT_NAME).write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (portfolio_dir / SEED_PROMPT_NAME).write_text(
        _render_seed_prompt(process, context, notes=notes),
        encoding="utf-8",
    )
    _append_decision_log(portfolio_dir, f"{now} portfolio workspace prepared by {prepared_by}")
    return manifest


def normalize_document_role(value: str | None) -> str:
    role = str(value or "").strip()
    if role in DOCUMENT_ROLE_LABELS:
        return role
    return "otros"


def infer_portfolio_scenario(process: Process, selected_documents: list[dict]) -> dict:
    roles = {
        str(doc.get("document_role") or "otros")
        for doc in selected_documents
        if isinstance(doc, dict)
    }
    queries_status = _queries_window_status(process)
    if "bases_aclaradas" in roles:
        scenario_id = "verify_integrated_bases"
        seed_variant = "bases_clarifications_integrated"
        action = (
            "Verificar que las aclaraciones fueron integradas correctamente en las bases "
            "aclaradas/integradas y completar brechas si hace falta."
        )
    elif "aclaraciones" in roles:
        scenario_id = "integrate_clarifications"
        seed_variant = "bases_plus_clarifications"
        action = (
            "Integrar aclaraciones/respuestas con las bases iniciales antes de continuar "
            "el trabajo de portafolio."
        )
    elif "bases_iniciales" not in roles and "especificaciones_tecnicas" in roles:
        scenario_id = "technical_specs_only"
        seed_variant = "initial_bases"
        action = (
            "Revisar especificaciones técnicas seleccionadas sin asumir que hay bases "
            "iniciales; pedir al usuario bases o aclaraciones si son necesarias."
        )
    else:
        scenario_id = "initial_bases"
        seed_variant = "initial_bases"
        if queries_status == "open":
            action = (
                "Trabajar sobre bases iniciales con etapa de consultas abierta; priorizar "
                "preguntas, observaciones y riesgos para el usuario."
            )
        else:
            action = (
                "Trabajar sobre bases iniciales sin aclaraciones seleccionadas; revisar alcance, "
                "riesgos y próximos pasos con el usuario."
            )
    return {
        "id": scenario_id,
        "seed_variant": seed_variant,
        "recommended_action": action,
        "queries_window": queries_status,
        "document_roles": sorted(roles),
    }


def _queries_window_status(process: Process) -> str:
    fechas = fechas_listado_from_cronograma_json(
        process.cronograma_json,
        fallback_consultas=process.fecha_consultas or "",
        fallback_presentacion=process.fecha_presentacion or "",
    )
    if not fechas.fecha_consultas:
        return "unknown"
    ts = parse_seace_datetime(fechas.fecha_consultas)
    if ts is None:
        return "unknown"
    now_ts = datetime.now(_LIMA_TZ).timestamp()
    return "open" if ts >= now_ts else "closed"


def parse_seace_datetime(value: str | None) -> float | None:
    if not value:
        return None
    match = _DATE_TIME_RE.match(value.strip())
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    try:
        dt = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
        )
    except ValueError:
        return None
    return dt.replace(tzinfo=_LIMA_TZ).timestamp()


def _clarifications_from_documents(selected_documents: list[dict]) -> list[dict]:
    clarifications: list[dict] = []
    for doc in selected_documents:
        if doc.get("document_role") != "aclaraciones":
            continue
        clarifications.append(
            {
                "file": str(doc.get("dest_path") or ""),
                "clarification_type": "aclaracion",
                "notes": "Clasificado por usuario en staging de portafolio",
            }
        )
    return clarifications


def _replace_inputs_dir(inputs_dir: Path, tmp_inputs: Path, portfolio_dir: Path) -> None:
    if inputs_dir.exists():
        backup_root = portfolio_dir / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"inputs_{utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        suffix = 1
        while backup.exists():
            suffix += 1
            backup = backup_root / f"inputs_{utcnow().strftime('%Y%m%dT%H%M%SZ')}_{suffix}"
        shutil.move(str(inputs_dir), str(backup))
    shutil.move(str(tmp_inputs), str(inputs_dir))


def _backup_if_exists(path: Path, portfolio_dir: Path) -> None:
    if not path.exists():
        return
    backup_root = portfolio_dir / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"{path.name}.{utcnow().strftime('%Y%m%dT%H%M%SZ')}.bak"
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = backup_root / f"{path.name}.{utcnow().strftime('%Y%m%dT%H%M%SZ')}_{suffix}.bak"
    shutil.copy2(path, backup)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel_to_process(proc_dir: Path, path: Path) -> str:
    return str(path.relative_to(proc_dir)).replace("\\", "/")


def _load_free_reader_profile(proc_dir: Path) -> dict:
    for path in (
        proc_dir / "pre_portafolio" / "fast_analysis" / "profile.json",
        proc_dir / "fast_analysis" / "profile.json",
    ):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        return raw if isinstance(raw, dict) else {}
    return {}


def _build_context(
    config: AppConfig,
    process: Process,
    proc_dir: Path,
    portfolio_dir: Path,
    scenario: dict,
) -> dict:
    return {
        "version": "0.1",
        "tenant_id": config.tenant_id,
        "process": {
            "id": process.id,
            "source": process.source,
            "source_ref": process.source_ref,
            "workflow_profile": process.workflow_profile,
            "nomenclatura": process.nomenclatura,
            "entity": process.entity.nombre if process.entity else None,
            "objeto": process.objeto,
            "descripcion": process.descripcion,
        },
        "paths": {
            "process_dir": str(proc_dir.resolve()),
            "portfolio_dir": str(portfolio_dir.resolve()),
            "inputs_dir": str((portfolio_dir / "inputs").resolve()),
            "manifest": str((portfolio_dir / MANIFEST_NAME).resolve()),
            "context": str((portfolio_dir / CONTEXT_NAME).resolve()),
            "seed_prompt": str((portfolio_dir / SEED_PROMPT_NAME).resolve()),
            "logs_dir": str((portfolio_dir / "logs").resolve()),
            "artifacts_dir": str((portfolio_dir / "artifacts").resolve()),
            "outputs_dir": str((portfolio_dir / "outputs").resolve()),
        },
        "portfolio_scenario": scenario,
        "instruction_paths": {
            "stage_c_orchestrator": "instrucciones/C_conversion/00_orquestador.md",
            "stage_c_runbook": "instrucciones/C_conversion/01_runbook.md",
            "stage_d_orchestrator": "instrucciones/D_portafolio/00_orquestador.md",
            "stage_d_runbook": "instrucciones/D_portafolio/01_runbook.md",
            "agent_patterns": "instrucciones/shared/agent_patterns.md",
            "model_routing": "instrucciones/shared/model_routing.yaml",
            "params": "instrucciones/shared/params.yaml",
        },
    }


def _render_seed_prompt(process: Process, context: dict, *, notes: str) -> str:
    paths = context["paths"]
    instruction_paths = context["instruction_paths"]
    scenario = context["portfolio_scenario"]
    notes_block = notes.strip() or "(sin notas adicionales)"
    variant_block = _seed_variant_instructions(scenario)
    return f"""# Seed prompt — portafolio {process.id}

Actúas como Hermes dentro del workspace de portafolio de una licitación. El portal ya hizo staging determinístico de documentos y preparó el contrato de archivos; desde aquí el trabajo es agéntico e interactivo.

## Proceso

- Entidad: {context["process"].get("entity") or "—"}
- Nomenclatura: {process.nomenclatura}
- Objeto: {process.objeto or "—"}
- Source: {process.source}
- Workflow profile: {process.workflow_profile}

## Escenario detectado

- Variante seed: `{scenario["seed_variant"]}`
- Escenario: `{scenario["id"]}`
- Estado ventana de consultas: `{scenario["queries_window"]}`
- Acción recomendada: {scenario["recommended_action"]}

## Paths del workspace

- Proceso: `{paths["process_dir"]}`
- Portafolio: `{paths["portfolio_dir"]}`
- Inputs: `{paths["inputs_dir"]}`
- Manifest staging: `{paths["manifest"]}`
- Contexto: `{paths["context"]}`
- Logs: `{paths["logs_dir"]}`
- Artifacts: `{paths["artifacts_dir"]}`
- Outputs: `{paths["outputs_dir"]}`

## Instrucciones operativas

1. Lee primero el manifest y el contexto.
2. Usa los runbooks como playbooks de trabajo, no como un pipeline rígido de backend.
3. No rehagas staging ni descarga documental; eso ya lo hizo el portal.
4. Si necesitas normalización/indexación determinística pendiente, usa la etapa C como guía y deja logs claros.
5. Para D, conversa con el usuario, decide el siguiente movimiento y ejecuta herramientas/scripts temporales solo cuando el caso lo justifique.
6. Preserva artefactos intermedios bajo `artifacts/` y resultados finales bajo `outputs/`.
7. Registra decisiones relevantes en `logs/decision_log.md`.

## Variante de arranque

{variant_block}

## Playbooks disponibles

- `{instruction_paths["stage_c_orchestrator"]}`
- `{instruction_paths["stage_c_runbook"]}`
- `{instruction_paths["stage_d_orchestrator"]}`
- `{instruction_paths["stage_d_runbook"]}`
- `{instruction_paths["agent_patterns"]}`
- `{instruction_paths["model_routing"]}`
- `{instruction_paths["params"]}`

## Notas del usuario/portal

{notes_block}
"""


def _seed_variant_instructions(scenario: dict) -> str:
    seed_variant = scenario.get("seed_variant")
    if seed_variant == "bases_plus_clarifications":
        return (
            "El usuario seleccionó bases iniciales y documentos de aclaraciones/respuestas. "
            "Tu primera tarea es revisar el manifest, ubicar ambos grupos documentales e integrar "
            "las aclaraciones sobre las bases antes de continuar con BOM, specs o búsqueda."
        )
    if seed_variant == "bases_clarifications_integrated":
        return (
            "El usuario seleccionó bases aclaradas/integradas. Tu primera tarea es verificar "
            "contra bases iniciales y aclaraciones disponibles que la integración sea correcta; "
            "si faltan aclaraciones o hay contradicciones, documenta brechas y propone corrección."
        )
    if scenario.get("queries_window") == "open":
        return (
            "El usuario seleccionó bases iniciales y la ventana de consultas parece abierta. "
            "Tu primera tarea es detectar ambigüedades, riesgos, requisitos imposibles o puntos "
            "que convenga preguntar/observar antes del cierre de consultas."
        )
    if "bases_iniciales" not in scenario.get("document_roles", []):
        return (
            "El usuario no clasificó documentos como bases iniciales. Tu primera tarea es revisar "
            "los documentos incluidos, explicar qué falta para un análisis completo y pedir bases, "
            "aclaraciones o confirmación antes de avanzar a integración/BOM."
        )
    return (
        "El usuario seleccionó bases iniciales sin aclaraciones clasificadas. Tu primera tarea es "
        "orientar el análisis inicial de portafolio y confirmar con el usuario si conviene buscar "
        "aclaraciones, preparar consultas u ordenar requisitos."
    )


def _append_decision_log(portfolio_dir: Path, line: str) -> None:
    log_path = portfolio_dir / "logs" / "decision_log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {line}\n")
