"""Ejecuta scripts externos de análisis (etapas 1 y 2)."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..client import ProcessRow, SeaceClient
from ..config import AppConfig
from ..db.models import AnalysisResult, Process, ProcessStatus, utcnow
from ..downloader import download_file
from ..parser import parse_ficha
from .tender_bridge import run_tender_stage1

logger = logging.getLogger(__name__)


def process_data_dir(config: AppConfig, process: Process) -> Path:
    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in process.nomenclatura
    )
    return config.data_dir / "procesos" / f"{process.nid_proceso}_{safe}"[:120]


class AnalysisRunner:
    """Descarga documentos y lanza scripts configurados por el usuario."""

    def __init__(self, config: AppConfig, session: Session) -> None:
        self.config = config
        self.session = session

    def analyze(self, process_id: int) -> AnalysisResult:
        process = self.session.get(Process, process_id)
        if process is None:
            raise ValueError(f"Proceso {process_id} no encontrado")

        entity = process.entity
        analysis = process.analysis
        if analysis is None:
            analysis = AnalysisResult(process_id=process.id, status="running")
            self.session.add(analysis)
        else:
            analysis.status = "running"
            analysis.error_message = None

        analysis.started_at = utcnow()
        self.session.flush()

        proc_dir = process_data_dir(self.config, process)
        docs_dir = proc_dir / "documentos"
        docs_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._download_documents(process, entity.ruc, docs_dir)
            result_data = self._run_pipeline(proc_dir, docs_dir)
            self._apply_result(analysis, result_data)
            process.status = ProcessStatus.analizada
            process.data_dir = str(proc_dir)
            analysis.status = "done"
            analysis.finished_at = utcnow()
        except Exception as exc:
            analysis.status = "error"
            analysis.error_message = str(exc)
            analysis.finished_at = utcnow()
            logger.exception("Análisis fallido para proceso %s", process_id)
            raise

        self.session.commit()
        return analysis

    def _download_documents(self, process: Process, ruc: str, docs_dir: Path) -> None:
        docs = json.loads(process.documentos_json or "[]")
        if not docs:
            if process.nid_convocatoria and process.link_id:
                docs = self._refresh_documentos(process, ruc)
            else:
                raise RuntimeError(
                    "Sin documentos en BD. Vuelve a escanear el proceso antes de analizar."
                )

        for doc in docs:
            uuid = doc["uuid"]
            nombre = doc.get("nombre", uuid)
            ext = Path(nombre).suffix or ".pdf"
            dest = docs_dir / f"{uuid}{ext}"
            if dest.exists():
                continue
            tipo = doc.get("tipo_descarga", "3")
            download_file(uuid, dest, guest=tipo != "3")
            logger.info("Descargado %s", nombre)

        manifest = docs_dir / "manifest.json"
        manifest.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")

    def _refresh_documentos(self, process: Process, ruc: str) -> list[dict]:
        from dataclasses import asdict

        from ..client import ProcessRow

        client = SeaceClient(ruc, process.anio, self.config.rows_per_page)
        row = ProcessRow(
            row_index=0,
            numero=process.numero or "",
            fecha_publicacion=process.fecha_publicacion or "",
            nomenclatura=process.nomenclatura,
            reiniciado_desde=process.reiniciado_desde or "",
            objeto=process.objeto or "",
            descripcion=process.descripcion or "",
            cuantia=process.cuantia or "",
            moneda=process.moneda or "",
            version_seace=process.version_seace or "",
            nid_proceso=process.nid_proceso,
            nid_convocatoria=process.nid_convocatoria or "",
            nid_sistema=process.nid_sistema or "3",
            link_id=process.link_id or "",
            ntipo=process.ntipo or "0",
        )
        ficha = client.open_ficha(row)
        parsed = parse_ficha(ficha.html, ficha.ficha_id, process.nid_proceso)
        docs = [asdict(d) for d in parsed.documentos]
        process.documentos_json = json.dumps(docs, ensure_ascii=False)
        self.session.flush()
        return docs

    def _run_pipeline(self, proc_dir: Path, documents_dir: Path) -> dict:
        """Ejecuta etapa análisis: tender_procurement 1.x o scripts legacy."""
        result: dict = {}
        cfg = self.config.analysis

        if cfg.tender.repo_path and cfg.tender.repo_path.exists():
            result["stage1"] = run_tender_stage1(
                self.config, proc_dir, documents_dir
            )
        elif cfg.stage1_script and cfg.stage1_script.exists():
            result["stage1"] = self._run_script(cfg.stage1_script, proc_dir)
        else:
            logger.warning("Sin tender_procurement ni stage1_script")
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
        """Resultado mínimo cuando aún no hay scripts del usuario."""
        docs = list((proc_dir / "documentos").glob("*"))
        return {
            "alcance": "(pendiente: configurar analysis.stage1_script)",
            "incluye": f"{len(docs)} documento(s) descargado(s)",
            "requisitos": "",
            "note": "Coloca tus scripts y define analysis_output.json en proc_dir",
        }

    def _apply_result(self, analysis: AnalysisResult, data: dict) -> None:
        stage1 = data.get("stage1", data)
        if isinstance(stage1, dict):
            axis0 = stage1.get("axis0")
            if isinstance(axis0, dict):
                analysis.alcance = axis0.get("alcance") or analysis.alcance
                analysis.incluye = axis0.get("incluye") or analysis.incluye
                analysis.requisitos = axis0.get("requisitos") or analysis.requisitos
            else:
                analysis.alcance = stage1.get("alcance") or analysis.alcance
                analysis.incluye = stage1.get("incluye") or analysis.incluye
                analysis.requisitos = stage1.get("requisitos") or analysis.requisitos

        stage2 = data.get("stage2")
        if isinstance(stage2, dict):
            analysis.entregables = stage2.get("entregables")
            analysis.equipos = stage2.get("equipos")

        analysis.raw_json = json.dumps(data, ensure_ascii=False, indent=2)
