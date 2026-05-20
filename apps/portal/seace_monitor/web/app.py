"""Aplicación web FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..analysis.runner import AnalysisRunner
from ..config import AppConfig
from ..db.models import AnalysisResult, Entity, Process, ProcessStatus
from ..db.session import init_db, session_factory

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
    ):
        q = db.query(Process).options(joinedload(Process.entity))
        if estado and estado in ProcessStatus.__members__:
            q = q.filter(Process.status == ProcessStatus(estado))
        if entidad:
            q = q.join(Entity).filter(Entity.ruc == entidad)
        processes = q.order_by(Process.fecha_publicacion.desc()).limit(500).all()
        entities = db.query(Entity).order_by(Entity.nombre).all()
        return render(
            request,
            "publicaciones.html",
            active_page="publicaciones",
            processes=processes,
            entities=entities,
            filtro_estado=estado or "",
            filtro_entidad=entidad or "",
            ProcessStatus=ProcessStatus,
            statuses=list(ProcessStatus),
        )

    @app.post("/publicaciones/{process_id}/analizar")
    def analizar(
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404, "Proceso no encontrado")

        def _job():
            session = session_factory()
            try:
                runner = AnalysisRunner(_config, session)
                runner.analyze(process_id)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Background analysis failed")
            finally:
                session.close()

        background_tasks.add_task(_job)
        return RedirectResponse("/publicaciones?msg=analisis_iniciado", status_code=303)

    @app.post("/publicaciones/{process_id}/estado")
    def cambiar_estado(
        process_id: int,
        estado: str = Form(...),
        db: Session = Depends(get_db),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if estado not in ProcessStatus.__members__:
            raise HTTPException(400, "Estado inválido")
        if (
            estado == ProcessStatus.portafolio.value
            and proc.status == ProcessStatus.publicada
        ):
            raise HTTPException(
                400, "Solo procesos analizados pueden pasar a portafolio"
            )
        proc.status = ProcessStatus(estado)
        db.commit()
        return RedirectResponse("/publicaciones", status_code=303)

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
        return render(
            request,
            "analizado_detalle.html",
            active_page="analizados",
            process=proc,
            ProcessStatus=ProcessStatus,
        )

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
