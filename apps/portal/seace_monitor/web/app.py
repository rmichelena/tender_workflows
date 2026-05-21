"""Aplicación web FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..analysis.runner import AnalysisRunner
from ..config import AppConfig
from ..db.models import AnalysisResult, Entity, Process, ProcessStatus, utcnow
from ..db.session import init_db, session_factory
from .detail_data import (
    download_filename_for_path,
    list_analyzable_files,
    list_downloaded_documents,
    parse_cronograma,
    resolve_document_path,
)
from .markdown_render import render_free_reader_summary
from .filters import publicaciones_query
from .seace_proxy import (
    new_session_id,
    proxy_seace_request,
    seace_open_redirect,
    seace_view_path,
)
from .seace_view import can_open_seace
from .sorting import (
    SORTABLE_COLUMNS,
    build_sort_query,
    normalize_dir,
    normalize_sort,
    sort_processes,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_config: AppConfig | None = None


def get_config() -> AppConfig:
    if _config is None:
        raise RuntimeError("App no inicializada")
    return _config


def get_db() -> Session:
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_app(config: AppConfig | None = None) -> FastAPI:
    global _config
    _config = config or AppConfig.load()
    init_db(_config.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info("SEACE Monitor web — DB: %s", _config.database_url)
        yield

    app = FastAPI(title="SEACE Monitor", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["can_open_seace"] = can_open_seace
    templates.env.globals["seace_view_path"] = seace_view_path

    def render(request: Request, name: str, **ctx):
        ctx["request"] = request
        ctx["active_page"] = ctx.get("active_page", "")
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, db: Session = Depends(get_db)):
        counts = {
            s.value: db.query(Process).filter(Process.status == s).count()
            for s in ProcessStatus
        }
        entities = db.query(Entity).count()
        recent = (
            db.query(Process)
            .options(joinedload(Process.entity))
            .order_by(Process.first_seen_at.desc())
            .limit(10)
            .all()
        )
        return render(
            request,
            "dashboard.html",
            active_page="dashboard",
            counts=counts,
            entities=entities,
            recent=recent,
            config=_config,
        )

    @app.get("/publicaciones", response_class=HTMLResponse)
    def publicaciones(
        request: Request,
        db: Session = Depends(get_db),
        estado: str | None = None,
        entidad: str | None = None,
        objeto: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
    ):
        q = (
            db.query(Process)
            .options(joinedload(Process.entity))
            .filter(
                Process.status.in_(
                    [ProcessStatus.publicada, ProcessStatus.descargando]
                )
            )
        )
        workflow_statuses = [ProcessStatus.publicada, ProcessStatus.descargando]
        if estado and estado in ProcessStatus.__members__:
            if estado != ProcessStatus.publicada.value:
                raise HTTPException(
                    400, "Publicaciones solo muestra procesos pendientes (publicada)"
                )
        if entidad:
            q = q.filter(
                Process.entity_id
                == db.query(Entity.id).filter(Entity.ruc == entidad).scalar_subquery()
            )
        if objeto:
            q = q.filter(Process.objeto == objeto)
        sort_col = normalize_sort(sort)
        sort_dir = normalize_dir(dir, sort_col)
        processes = sort_processes(q.limit(500).all(), sort_col, sort_dir)
        entities = (
            db.query(Entity)
            .join(Process, Process.entity_id == Entity.id)
            .filter(
                Process.status.in_(
                    [ProcessStatus.publicada, ProcessStatus.descargando]
                )
            )
            .distinct()
            .order_by(Entity.nombre)
            .all()
        )
        objetos = [
            row[0]
            for row in (
                db.query(Process.objeto)
                .filter(
                    Process.status.in_(
                        [ProcessStatus.publicada, ProcessStatus.descargando]
                    ),
                    Process.objeto.isnot(None),
                    Process.objeto != "",
                )
                .distinct()
                .order_by(Process.objeto)
                .all()
            )
        ]
        filtro_estado = estado or ""
        filtro_entidad = entidad or ""
        filtro_objeto = objeto or ""

        def sort_href(column: str) -> str:
            return build_sort_query(
                column,
                sort=sort_col,
                direction=sort_dir,
                estado=filtro_estado,
                entidad=filtro_entidad,
                objeto=filtro_objeto,
            )

        return render(
            request,
            "publicaciones.html",
            active_page="publicaciones",
            processes=processes,
            entities=entities,
            objetos=objetos,
            filtro_estado=filtro_estado,
            filtro_entidad=filtro_entidad,
            filtro_objeto=filtro_objeto,
            sort=sort_col,
            sort_dir=sort_dir,
            sort_columns=SORTABLE_COLUMNS,
            sort_href=sort_href,
            ProcessStatus=ProcessStatus,
            statuses=workflow_statuses,
        )

    def _filter_redirect(
        entidad: str = "",
        objeto: str = "",
        sort: str = "",
        dir: str = "",
        msg: str = "",
        scroll: str = "",
    ) -> RedirectResponse:
        return RedirectResponse(
            publicaciones_query(
                entidad=entidad,
                objeto=objeto,
                sort=sort,
                dir=dir,
                msg=msg,
                scroll=scroll,
            ),
            status_code=303,
        )

    @app.post("/publicaciones/{process_id}/descartar")
    def descartar(
        process_id: int,
        db: Session = Depends(get_db),
        entidad: str = Form(""),
        objeto: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
        scroll: str = Form(""),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status == ProcessStatus.descartada:
            return _filter_redirect(
                entidad=entidad, objeto=objeto, sort=sort, dir=dir, scroll=scroll
            )
        if proc.status == ProcessStatus.descargando:
            raise HTTPException(409, "Descarga en curso")
        proc.status = ProcessStatus.descartada
        return _filter_redirect(
            entidad=entidad, objeto=objeto, sort=sort, dir=dir, scroll=scroll
        )

    @app.get("/descartados", response_class=HTMLResponse)
    def descartados(request: Request, db: Session = Depends(get_db)):
        rows = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.status == ProcessStatus.descartada)
            .order_by(Process.updated_at.desc())
            .all()
        )
        return render(
            request,
            "descartados.html",
            active_page="descartados",
            processes=rows,
        )

    @app.post("/descartados/{process_id}/restaurar")
    def restaurar(process_id: int, db: Session = Depends(get_db)):
        proc = (
            db.query(Process)
            .options(joinedload(Process.analysis))
            .filter(Process.id == process_id)
            .one_or_none()
        )
        if proc is None:
            raise HTTPException(404)
        if proc.analysis and proc.analysis.status == "done":
            proc.status = ProcessStatus.analizada
        elif proc.data_dir:
            proc.status = ProcessStatus.descargada
        else:
            proc.status = ProcessStatus.publicada
        db.commit()
        return RedirectResponse("/descartados", status_code=303)

    @app.post("/publicaciones/{process_id}/descargar")
    def descargar(
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        entidad: str = Form(""),
        objeto: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
        scroll: str = Form(""),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404, "Proceso no encontrado")
        if proc.status == ProcessStatus.descargada:
            return RedirectResponse(f"/descargados/{process_id}", status_code=303)
        if proc.status == ProcessStatus.descargando:
            return _filter_redirect(
                entidad=entidad,
                objeto=objeto,
                sort=sort,
                dir=dir,
                scroll=scroll,
            )
        if proc.status != ProcessStatus.publicada:
            if proc.status in (ProcessStatus.analizada, ProcessStatus.portafolio):
                return RedirectResponse(f"/analizados/{process_id}", status_code=303)
            raise HTTPException(400, "Estado no válido para descarga")

        proc.status = ProcessStatus.descargando
        db.commit()
        db.expunge(proc)

        def _job():
            session = session_factory()
            try:
                runner = AnalysisRunner(_config, session)
                runner.download(process_id)
            except Exception:
                proc_fail = session.get(Process, process_id)
                if proc_fail is not None:
                    proc_fail.status = ProcessStatus.publicada
                    session.commit()
                logger.exception("Background download failed")
            finally:
                session.close()

        background_tasks.add_task(_job)
        return _filter_redirect(
            entidad=entidad,
            objeto=objeto,
            sort=sort,
            dir=dir,
            scroll=scroll,
        )

    @app.get("/descargados", response_class=HTMLResponse)
    def descargados(request: Request, db: Session = Depends(get_db)):
        rows = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.status == ProcessStatus.descargada)
            .order_by(Process.updated_at.desc())
            .all()
        )
        return render(
            request,
            "descargados.html",
            active_page="descargados",
            processes=rows,
        )

    @app.get("/descargados/{process_id}", response_class=HTMLResponse)
    def descargado_detalle(request: Request, process_id: int, db: Session = Depends(get_db)):
        proc = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.id == process_id)
            .one_or_none()
        )
        if proc is None:
            raise HTTPException(404)
        if proc.status not in (ProcessStatus.descargada, ProcessStatus.analizada):
            if proc.status == ProcessStatus.publicada:
                return RedirectResponse(f"/publicaciones", status_code=303)
            raise HTTPException(404)
        archivos = list_analyzable_files(proc)
        cronograma = parse_cronograma(proc.cronograma_json)
        return render(
            request,
            "descargado_detalle.html",
            active_page="descargados",
            process=proc,
            archivos=archivos,
            cronograma=cronograma,
            ProcessStatus=ProcessStatus,
        )

    @app.post("/descargados/{process_id}/analizar")
    async def analizar_descargado(
        request: Request,
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status != ProcessStatus.descargada:
            raise HTTPException(400, "Solo procesos descargados pueden analizarse")
        if proc.analysis and proc.analysis.status == "running":
            return RedirectResponse(
                f"/descargados/{process_id}?msg=analisis_iniciado",
                status_code=303,
            )

        form = await request.form()
        selected = [
            str(value).strip()
            for value in form.getlist("selected_files")
            if str(value).strip()
        ]
        if not selected:
            return RedirectResponse(
                f"/descargados/{process_id}?msg=selecciona_archivos",
                status_code=303,
            )

        analysis = proc.analysis
        if analysis is None:
            analysis = AnalysisResult(process_id=proc.id, status="running")
            db.add(analysis)
        else:
            analysis.status = "running"
            analysis.error_message = None
        analysis.started_at = utcnow()
        db.commit()
        db.expunge_all()

        selected_paths = list(selected)

        def _job():
            session = session_factory()
            try:
                runner = AnalysisRunner(_config, session)
                runner.analyze(process_id, selected_paths)
            except Exception:
                logger.exception("Background analysis failed")
            finally:
                session.close()

        background_tasks.add_task(_job)
        return RedirectResponse(
            f"/descargados/{process_id}?msg=analisis_iniciado",
            status_code=303,
        )

    @app.post("/descargados/{process_id}/descartar")
    def descartar_descargado(process_id: int, db: Session = Depends(get_db)):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status != ProcessStatus.descargada:
            raise HTTPException(400, "Solo procesos descargados")
        proc.status = ProcessStatus.descartada
        db.commit()
        return RedirectResponse("/descargados", status_code=303)

    @app.get("/descargados/{process_id}/documentos/{filename:path}")
    def descargar_documento_descargado(
        process_id: int, filename: str, db: Session = Depends(get_db)
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        path = resolve_document_path(proc, filename)
        if path is None:
            raise HTTPException(404, "Documento no encontrado")
        return FileResponse(path, filename=download_filename_for_path(proc, path))

    @app.get("/seace/open/{process_id}")
    def seace_open(process_id: int, request: Request, db: Session = Depends(get_db)):
        proc = (
            db.query(Process)
            .options(joinedload(Process.entity))
            .filter(Process.id == process_id)
            .one_or_none()
        )
        if proc is None:
            raise HTTPException(404)
        if not can_open_seace(proc):
            raise HTTPException(
                400,
                "Sin metadatos JSF para abrir SEACE. Vuelve a escanear el proceso.",
            )
        sid = request.cookies.get("seace_sid") or new_session_id()
        return seace_open_redirect(proc, sid=sid)

    @app.api_route("/seace/p/{path:path}", methods=["GET", "POST", "HEAD"])
    async def seace_proxy(
        request: Request,
        path: str,
        db: Session = Depends(get_db),
        seace_open: str | None = None,
    ):
        process_for_open = None
        open_id = seace_open or request.query_params.get("seace_open")
        if open_id:
            try:
                process_for_open = (
                    db.query(Process)
                    .options(joinedload(Process.entity))
                    .filter(Process.id == int(open_id))
                    .one_or_none()
                )
            except ValueError:
                process_for_open = None
        body = await request.body() if request.method == "POST" else None
        return proxy_seace_request(
            request,
            path,
            _config,
            process_for_open if process_for_open and can_open_seace(process_for_open) else None,
            body=body,
        )

    @app.post("/analizados/{process_id}/descartar")
    def descartar_analizado(process_id: int, db: Session = Depends(get_db)):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status not in (ProcessStatus.analizada, ProcessStatus.portafolio):
            raise HTTPException(400, "Solo procesos analizados o en portafolio")
        proc.status = ProcessStatus.descartada
        db.commit()
        return RedirectResponse("/analizados", status_code=303)

    @app.post("/analizados/{process_id}/estado")
    def cambiar_estado_analizados(
        process_id: int,
        estado: str = Form(...),
        db: Session = Depends(get_db),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if estado not in (ProcessStatus.analizada.value, ProcessStatus.portafolio.value):
            raise HTTPException(400, "Estado inválido")
        if proc.status not in (ProcessStatus.analizada, ProcessStatus.portafolio):
            raise HTTPException(400, "Solo procesos analizados o en portafolio")
        proc.status = ProcessStatus(estado)
        db.commit()
        return RedirectResponse("/analizados", status_code=303)

    @app.get("/analizados", response_class=HTMLResponse)
    def analizados(request: Request, db: Session = Depends(get_db)):
        rows = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.status.in_([ProcessStatus.analizada, ProcessStatus.portafolio]))
            .order_by(Process.updated_at.desc())
            .all()
        )
        return render(
            request,
            "analizados.html",
            active_page="analizados",
            processes=rows,
            ProcessStatus=ProcessStatus,
        )

    @app.get("/analizados/{process_id}", response_class=HTMLResponse)
    def analizado_detalle(request: Request, process_id: int, db: Session = Depends(get_db)):
        proc = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.id == process_id)
            .one_or_none()
        )
        if proc is None:
            raise HTTPException(404)
        documentos = list_downloaded_documents(proc)
        cronograma = parse_cronograma(proc.cronograma_json)
        free_reader_html = None
        if proc.data_dir:
            summary_path = Path(proc.data_dir) / "free_reader_summary.md"
            if summary_path.is_file():
                free_reader_html = render_free_reader_summary(
                    summary_path.read_text(encoding="utf-8")
                )
        return render(
            request,
            "analizado_detalle.html",
            active_page="analizados",
            process=proc,
            documentos=documentos,
            cronograma=cronograma,
            free_reader_html=free_reader_html,
            ProcessStatus=ProcessStatus,
        )

    @app.get("/analizados/{process_id}/preview/{filename:path}")
    def preview_documento(process_id: int, filename: str, db: Session = Depends(get_db)):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        path = resolve_document_path(proc, filename)
        if path is None:
            raise HTTPException(404, "Documento no encontrado")
        if path.suffix.lower() != ".pdf":
            raise HTTPException(400, "Preview solo disponible para PDF")
        display_name = download_filename_for_path(proc, path)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=display_name,
            headers={"Content-Disposition": f'inline; filename="{display_name}"'},
        )

    @app.get("/analizados/{process_id}/documentos/{filename:path}")
    def descargar_documento(process_id: int, filename: str, db: Session = Depends(get_db)):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        path = resolve_document_path(proc, filename)
        if path is None:
            raise HTTPException(404, "Documento no encontrado")
        return FileResponse(path, filename=download_filename_for_path(proc, path))

    @app.get("/api/processes/{process_id}/workflow")
    def api_process_workflow(process_id: int, db: Session = Depends(get_db)):
        proc = (
            db.query(Process)
            .options(joinedload(Process.analysis))
            .filter(Process.id == process_id)
            .one_or_none()
        )
        if proc is None:
            raise HTTPException(404)
        return {
            "id": proc.id,
            "status": proc.status.value,
            "analysis_status": proc.analysis.status if proc.analysis else None,
        }

    @app.get("/api/stats")
    def api_stats(db: Session = Depends(get_db)):
        by_status = (
            db.query(Process.status, func.count(Process.id))
            .group_by(Process.status)
            .all()
        )
        return {
            "by_status": {s.value: c for s, c in by_status},
            "entities": db.query(Entity).count(),
            "total": db.query(Process).count(),
        }

    return app
