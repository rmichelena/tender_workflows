"""Preparación determinística del workspace de portafolio."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from .config import AppConfig
from .db.models import Process, utcnow
from .analysis.document_prep import resolve_selected_documents


MANIFEST_NAME = "staging_manifest.json"
SEED_PROMPT_NAME = "seed_prompt.md"
CONTEXT_NAME = "context.json"


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
    try:
        for source_path in selected_paths:
            rel = source_path.relative_to(docs_dir)
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
                }
            )
        _replace_inputs_dir(inputs_dir, tmp_inputs, portfolio_dir)
    except Exception:
        shutil.rmtree(tmp_inputs, ignore_errors=True)
        raise

    now = utcnow().astimezone(timezone.utc).isoformat()
    manifest = {
        "version": "0.1",
        "process_id": process.id,
        "tenant_id": config.tenant_id,
        "source": process.source,
        "selected_documents": selected_documents,
        "uploads": [],
        "clarifications": [],
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

    context = _build_context(config, process, proc_dir, portfolio_dir)
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
    notes_block = notes.strip() or "(sin notas adicionales)"
    return f"""# Seed prompt — portafolio {process.id}

Actúas como Hermes dentro del workspace de portafolio de una licitación. El portal ya hizo staging determinístico de documentos y preparó el contrato de archivos; desde aquí el trabajo es agéntico e interactivo.

## Proceso

- Entidad: {context["process"].get("entity") or "—"}
- Nomenclatura: {process.nomenclatura}
- Objeto: {process.objeto or "—"}
- Source: {process.source}
- Workflow profile: {process.workflow_profile}

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


def _append_decision_log(portfolio_dir: Path, line: str) -> None:
    log_path = portfolio_dir / "logs" / "decision_log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {line}\n")
