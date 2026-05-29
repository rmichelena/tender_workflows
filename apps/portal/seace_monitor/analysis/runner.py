"""Ejecuta descarga de documentos y análisis (fast-path o scripts externos)."""

from __future__ import annotations

import json
import logging
import subprocess
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.models import AnalysisResult, Process, ProcessStatus, utcnow
from ..list_order import (
    enter_analizados_list,
    enter_descargados_list,
    leave_analizados_list,
    leave_descargados_list,
)
from ..process_storage import delete_process_data_dir, clear_process_download_metadata
from ..tenant_paths import procesos_root
from ..document_storage import (
    cleanup_partial_downloads,
    download_and_store_document,
    normalize_legacy_filenames,
    prefer_canonical_archivo,
    write_manifest,
)
from ..parser import extract_cronograma_fechas, parse_ficha
from ..seace_search import (
    apply_list_row_to_process,
    open_ficha_for_process,
)
from .analysis_lock import AnalysisBusyError, analysis_lock
from .document_prep import extract_archives, resolve_selected_documents
from .fast_reader import run_fast_analysis
from .tender_bridge import run_tender_stage1
from ..web.detail_data import save_analyzed_files

logger = logging.getLogger(__name__)

_ANALYSIS_SNAPSHOT_FIELDS = (
    # run_id must stay here so rerun failure restores the pre-run identity.
    "status",
    "error_message",
    "alcance",
    "incluye",
    "requisitos",
    "entregables",
    "equipos",
    "raw_json",
    "finished_at",
    "run_id",
)


def process_data_dir(config: AppConfig, process: Process) -> Path:
    nid = (process.nid_proceso or str(process.id)).strip()
    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in process.nomenclatura
    ).strip("_")
    if safe:
        name = f"{nid}_{safe[:80]}"
    else:
        name = nid
    if len(name) > 120:
        digest = hashlib.sha256(name.encode()).hexdigest()[:8]
        name = f"{nid}_{safe[:60]}_{digest}"[:120]
    return procesos_root(config) / name


class AnalysisRunner:
    """Descarga documentos y lanza análisis configurado."""

    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session

    def download(self, process_id: int) -> Process:
        process = self.session.get(Process, process_id)
        if process is None:
            raise ValueError(f"Proceso {process_id} no encontrado")
        if process.status not in (
            ProcessStatus.descargando,
            ProcessStatus.publicada,
        ):
            raise RuntimeError(
                f"Estado inválido para descarga: {process.status.value}"
            )

        entity = process.entity
        proc_dir = process_data_dir(self.config, process)
        docs_dir = proc_dir / "documentos"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Liberar SQLite antes del fetch SEACE (puede tardar varios segundos).
        self.session.commit()

        docs = self._fetch_documentos_from_seace(process, entity.ruc)
        self.session.commit()

        try:
            self._fetch_documents(docs, docs_dir)
            extract_archives(docs_dir)
        except Exception:
            cleanup_partial_downloads(docs_dir)
            process = self.session.get(Process, process_id)
            if process is not None:
                delete_process_data_dir(self.config, process)
                clear_process_download_metadata(process)
                if process.status == ProcessStatus.descargando:
                    process.status = ProcessStatus.publicada
                self.session.commit()
            raise

        process = self.session.get(Process, process_id)
        if process is None:
            raise ValueError(f"Proceso {process_id} no encontrado")
        process.documentos_json = json.dumps(docs, ensure_ascii=False)
        process.status = ProcessStatus.descargada
        process.data_dir = str(proc_dir)
        process.watch_checked_at = utcnow()
        enter_descargados_list(self.session, process)
        self.session.commit()
        logger.info("Descarga completada para proceso %s → %s", process_id, proc_dir)
        return process

    def analyze(
        self,
        process_id: int,
        selected_rel_paths: list[str],
        *,
        run_id: str | None = None,
        prior_snapshot: dict | None = None,
    ) -> AnalysisResult:
        process = self.session.get(Process, process_id)
        if process is None:
            raise ValueError(f"Proceso {process_id} no encontrado")

        if process.status not in (ProcessStatus.descargada, ProcessStatus.analizada):
            raise RuntimeError(
                f"El proceso debe estar descargado antes de analizar (estado: {process.status.value})"
            )
        if not process.data_dir:
            raise RuntimeError("Sin data_dir; descarga los documentos primero.")
        if not selected_rel_paths:
            raise RuntimeError("Selecciona al menos un archivo para analizar.")

        proc_dir = Path(process.data_dir)
        docs_dir = proc_dir / "documentos"

        analysis = process.analysis
        prior = prior_snapshot
        if prior is None and analysis is not None and analysis.status == "done":
            prior = self._analysis_snapshot(analysis)
        if analysis is None:
            analysis = AnalysisResult(process_id=process.id, status="running")
            self.session.add(analysis)
        else:
            self._mark_analysis_running(analysis, run_id)

        if process.status == ProcessStatus.analizada:
            leave_analizados_list(self.session, process)
            process.status = ProcessStatus.descargada
            enter_descargados_list(self.session, process)
        self.session.commit()

        try:
            with analysis_lock(proc_dir):
                result_data = self._run_pipeline(
                    proc_dir, docs_dir, process, selected_rel_paths
                )
                if not self._is_current_run(process_id, run_id):
                    logger.warning(
                        "Análisis obsoleto descartado para proceso %s (run_id=%s)",
                        process_id,
                        run_id,
                    )
                    return analysis
                process = self.session.get(Process, process_id)
                if process is None:
                    raise RuntimeError(
                        f"Proceso {process_id} desapareció durante el análisis"
                    )
                analysis = process.analysis
                if analysis is None:
                    raise RuntimeError(
                        f"Sin registro de análisis para proceso {process_id}"
                    )
                self._apply_result(analysis, result_data)
                leave_descargados_list(self.session, process)
                process.status = ProcessStatus.analizada
                enter_analizados_list(self.session, process)
                analysis.status = "done"
                analysis.finished_at = utcnow()
                save_analyzed_files(proc_dir, selected_rel_paths)
        except AnalysisBusyError:
            raise
        except Exception as exc:
            if self._is_current_run(process_id, run_id):
                process = self.session.get(Process, process_id)
                if process is not None:
                    analysis = process.analysis
                    if analysis is not None:
                        if prior is not None:
                            self._restore_analysis_snapshot(analysis, prior)
                            leave_descargados_list(self.session, process)
                            process.status = ProcessStatus.analizada
                            enter_analizados_list(self.session, process)
                            err_path = proc_dir / "fast_analysis" / "last_rerun_error.txt"
                            err_path.parent.mkdir(parents=True, exist_ok=True)
                            err_path.write_text(str(exc), encoding="utf-8")
                        else:
                            analysis.status = "error"
                            analysis.error_message = str(exc)
                            analysis.finished_at = utcnow()
                            process.status = ProcessStatus.descargada
                logger.exception("Análisis fallido para proceso %s", process_id)
                self.session.commit()
            else:
                logger.warning(
                    "Error de análisis obsoleto ignorado para proceso %s",
                    process_id,
                )
            raise

        self.session.commit()
        return analysis

    @staticmethod
    def _analysis_snapshot(analysis: AnalysisResult) -> dict:
        return {field: getattr(analysis, field) for field in _ANALYSIS_SNAPSHOT_FIELDS}

    @staticmethod
    def _restore_analysis_snapshot(analysis: AnalysisResult, snap: dict) -> None:
        for field, value in snap.items():
            setattr(analysis, field, value)

    @staticmethod
    def _mark_analysis_running(
        analysis: AnalysisResult, run_id: str | None
    ) -> None:
        analysis.status = "running"
        analysis.error_message = None
        analysis.started_at = utcnow()
        analysis.finished_at = None
        if run_id:
            analysis.run_id = run_id

    def _is_current_run(self, process_id: int, run_id: str | None) -> bool:
        if not run_id:
            return True
        analysis = (
            self.session.query(AnalysisResult)
            .filter(AnalysisResult.process_id == process_id)
            .one_or_none()
        )
        return (
            analysis is not None
            and analysis.run_id == run_id
            and analysis.status == "running"
        )

    def _resolve_document_list(self, process: Process, ruc: str) -> list[dict]:
        if not process.nid_convocatoria or not process.link_id:
            raise RuntimeError(
                "Sin metadatos SEACE para abrir la ficha. Vuelve a escanear el proceso."
            )
        return self._fetch_documentos_from_seace(process, ruc)

    def _fetch_documents(self, docs: list[dict], docs_dir: Path) -> None:
        for doc in docs:
            tipo = doc.get("tipo_descarga", "3")
            if download_and_store_document(
                docs_dir,
                doc,
                guest=tipo != "3",
                http_proxy=self.config.http_proxy,
            ):
                logger.info(
                    "Descargado %s → %s",
                    doc.get("uuid", ""),
                    doc.get("archivo", ""),
                )

        normalize_legacy_filenames(docs_dir, docs)
        for doc in docs:
            prefer_canonical_archivo(docs_dir, doc)
        write_manifest(docs_dir, docs)

    def _fetch_documentos_from_seace(self, process: Process, ruc: str) -> list[dict]:
        from dataclasses import asdict

        row, ficha, client = open_ficha_for_process(self.config, process)
        parsed = parse_ficha(
            ficha.html,
            ficha.ficha_id,
            row.nid_proceso,
            http_session=client.session,
            ficha_url=ficha.url,
        )
        fechas = extract_cronograma_fechas(parsed.cronograma)
        apply_list_row_to_process(process, row)
        if not process.fecha_publicacion and parsed.fecha_publicacion:
            process.fecha_publicacion = parsed.fecha_publicacion
        process.fecha_consultas = fechas.fecha_consultas
        process.fecha_presentacion = fechas.fecha_presentacion
        process.cronograma_json = json.dumps(
            [asdict(c) for c in parsed.cronograma], ensure_ascii=False
        )
        process.ficha_id = parsed.ficha_id
        process.ficha_url = ficha.url
        process.content_hash = parsed.content_hash()
        return [asdict(d) for d in parsed.documentos]

    def _refresh_documentos(self, process: Process, ruc: str) -> list[dict]:
        docs = self._fetch_documentos_from_seace(process, ruc)
        process.documentos_json = json.dumps(docs, ensure_ascii=False)
        self.session.flush()
        return docs

    def _run_pipeline(
        self,
        proc_dir: Path,
        documents_dir: Path,
        process: Process,
        selected_rel_paths: list[str],
    ) -> dict:
        """Ejecuta fast-path Gemini o pipeline tender_procurement legacy."""
        result: dict = {}
        cfg = self.config.analysis

        if cfg.fast_path.enabled:
            logger.info(
                "Análisis fast-path Gemini para proceso %s (%s archivo(s))",
                process.id,
                len(selected_rel_paths),
            )
            result["stage1"] = run_fast_analysis(
                self.config,
                proc_dir,
                documents_dir,
                process,
                selected_rel_paths,
            )
        elif cfg.tender.repo_path and cfg.tender.repo_path.exists():
            result["stage1"] = run_tender_stage1(
                self.config, proc_dir, documents_dir
            )
        elif cfg.stage1_script and cfg.stage1_script.exists():
            result["stage1"] = self._run_script(cfg.stage1_script, proc_dir)
        else:
            logger.warning("Sin fast_path, tender_procurement ni stage1_script")
            result["stage1"] = self._placeholder_result(proc_dir)

        if cfg.stage2_script and cfg.stage2_script.exists():
            result["stage2"] = self._run_script(cfg.stage2_script, proc_dir)

        return result

    def _run_script(self, script: Path, proc_dir: Path) -> dict:
        logger.info("Ejecutando %s en %s", script, proc_dir)
        proc = subprocess.run(
            [str(script), str(proc_dir)],
            capture_output=True,
            text=True,
            timeout=self.config.analysis.scripts_timeout_seconds,
            cwd=proc_dir,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Script {script.name} falló ({proc.returncode}): {proc.stderr[-2000:]}"
            )

        output_file = proc_dir / "analysis_output.json"
        if output_file.exists():
            return json.loads(output_file.read_text(encoding="utf-8"))

        if proc.stdout.strip():
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                return {"raw_stdout": proc.stdout}

        return {"ok": True}

    def _placeholder_result(self, proc_dir: Path) -> dict:
        docs = list((proc_dir / "documentos").glob("*"))
        return {
            "alcance": "(pendiente: configurar analysis.stage1_script)",
            "incluye": f"{len(docs)} documento(s) descargado(s)",
            "requisitos": "",
            "note": "Coloca tus scripts y define analysis_output.json en proc_dir",
        }

    @staticmethod
    def _reset_analysis_for_rerun(analysis: AnalysisResult) -> None:
        analysis.status = "running"
        analysis.run_id = None
        analysis.error_message = None
        analysis.alcance = None
        analysis.incluye = None
        analysis.requisitos = None
        analysis.entregables = None
        analysis.equipos = None
        analysis.raw_json = None
        analysis.finished_at = None

    def _apply_result(self, analysis: AnalysisResult, data: dict) -> None:
        stage1 = data.get("stage1", data)
        if isinstance(stage1, dict):
            if stage1.get("mode") == "fast_gemini":
                if "alcance" in stage1:
                    analysis.alcance = stage1.get("alcance")
                if "incluye" in stage1:
                    analysis.incluye = stage1.get("incluye")
                if "requisitos" in stage1:
                    analysis.requisitos = stage1.get("requisitos")
            else:
                axis0 = stage1.get("axis0")
                if isinstance(axis0, dict):
                    if "alcance" in axis0:
                        analysis.alcance = axis0.get("alcance")
                    if "incluye" in axis0:
                        analysis.incluye = axis0.get("incluye")
                    if "requisitos" in axis0:
                        analysis.requisitos = axis0.get("requisitos")
                else:
                    if "alcance" in stage1:
                        analysis.alcance = stage1.get("alcance")
                    if "incluye" in stage1:
                        analysis.incluye = stage1.get("incluye")
                    if "requisitos" in stage1:
                        analysis.requisitos = stage1.get("requisitos")

        stage2 = data.get("stage2")
        if isinstance(stage2, dict):
            analysis.entregables = stage2.get("entregables")
            analysis.equipos = stage2.get("equipos")

        analysis.raw_json = json.dumps(data, ensure_ascii=False, indent=2)
