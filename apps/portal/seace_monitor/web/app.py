"""Aplicación web FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..analysis.runner import AnalysisRunner
from ..analysis.analysis_lock import AnalysisBusyError
from ..config import AppConfig
from ..db.list_views import build_process_list_views
from ..db.maintenance import (
    abandon_stale_analysis_run,
    is_stale_running_analysis,
    recover_stale_analyses,
)
from ..db.models import AnalysisResult, Entity, InterestStatus, Process, ProcessStatus
from ..db.session import init_db, session_factory
from ..process_storage import (
    clear_process_download_metadata,
    delete_process_analysis,
    purge_all_stale_process_data,
    recover_stale_downloads,
    recover_stale_workflow_transitions,
    repair_archived_processes,
    repair_discarded_processes,
    repair_processes_missing_data,
    restore_archived_process,
    resolve_restore_status,
)
from ..watchlist import mark_watchlist_read, watchlist_nav_badges
from ..tenant_paths import migrate_legacy_layout, migrate_process_data_dir_refs
from .detail_data import (
    build_document_tree,
    count_document_nodes,
    filter_new_document_nodes,
    flatten_selectable_leaves,
    load_analysis_selection,
    load_analyzed_files,
    parse_cronograma,
    parse_watch_changelog,
    save_analysis_selection,
)
from .markdown_render import render_free_reader_summary
from .filters import publicaciones_query, workflow_list_query
from .seace_proxy import (
    new_session_id,
    periodic_session_cleanup,
    proxy_seace_request,
    seace_open_redirect,
    seace_view_path,
)
from .seace_view import can_open_seace
from .analysis_chat import register_analysis_chat_routes
from .document_routes import register_process_document_routes
from .list_pages import render_workflow_list
from .process_queries import get_process_or_404
from .settings_autoreject import register_autoreject_settings_routes
from .settings_entities import bootstrap_entities, register_settings_routes
from .workflow_transitions import (
    archive_work,
    begin_archive_transition,
    begin_discard_transition,
    begin_download_transition,
    discard_work,
    schedule_download,
    schedule_status_transition,
)
from .sorting import (
    PUBLICACIONES_SORT_COLUMNS,
    SORTABLE_COLUMNS,
    build_sort_query,
    normalize_dir,
    normalize_sort,
    normalize_sort_for_columns,
    sort_process_list_views,
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


def _build_analisis_detail_context(proc: Process, *, mark_read: bool) -> dict:
    prev_cron = proc.watch_cronograma_prev_json if proc.watch_unread else None
    prev_docs = proc.watch_documentos_prev_json if proc.watch_unread else None
    if mark_read and proc.watch_unread:
        mark_watchlist_read(proc)
    analyzed_paths = None
    if proc.data_dir and proc.analysis and proc.analysis.status == "done":
        analyzed_paths = load_analyzed_files(Path(proc.data_dir))
    documentos = build_document_tree(
        proc,
        prev_documentos_json=prev_docs,
        apply_default_selection=False,
        analyzed_paths=analyzed_paths,
    )
    cronograma = parse_cronograma(
        proc.cronograma_json, prev_cronograma_json=prev_cron
    )
    archivos = filter_new_document_nodes(documentos) if prev_docs else []
    free_reader_html = None
    chat_payload = {"available": False, "turns": [], "message": None}
    if proc.data_dir:
        proc_dir = Path(proc.data_dir)
        summary_path = proc_dir / "free_reader_summary.md"
        if summary_path.is_file():
            free_reader_html = render_free_reader_summary(
                summary_path.read_text(encoding="utf-8")
            )
        from .analysis_chat import api_chat_payload
        from ..analysis.gemini_session import load_session

        chat_payload = api_chat_payload(load_session(proc_dir))
    return {
        "process": proc,
        "documentos": documentos,
        "documento_count": count_document_nodes(documentos),
        "archivos": archivos,
        "cronograma": cronograma,
        "had_watch_updates": bool(prev_cron or prev_docs),
        "free_reader_html": free_reader_html,
        "chat": chat_payload,
        "watch_changelog": parse_watch_changelog(proc.watch_changelog_json),
    }


def create_app(config: AppConfig | None = None) -> FastAPI:
    global _config
    _config = config or AppConfig.load()
    init_db(_config.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "SEACE Monitor web — DB: %s tenant: %s",
            _config.database_url,
            _config.tenant_id,
        )
        if migrate_legacy_layout(_config):
            logger.info("Layout de datos migrado a tenants/%s/", _config.tenant_id)
        db = session_factory()
        try:
            bootstrap_entities(db, _config)
            path_updates = migrate_process_data_dir_refs(db, _config)
            if path_updates:
                db.commit()
                logger.info(
                    "Actualizadas %s rutas data_dir tras migración de layout",
                    path_updates,
                )
            recovered = recover_stale_analyses(
                db, _config.stale_analysis_seconds, config=_config
            )
            if recovered:
                logger.warning(
                    "Recuperados %s análisis en estado running obsoletos",
                    recovered,
                )
            downloads_recovered = recover_stale_downloads(
                _config, db, _config.stale_analysis_seconds
            )
            if downloads_recovered:
                logger.warning(
                    "Recuperadas %s descargas obsoletas en descargando",
                    downloads_recovered,
                )
            transitions_recovered = recover_stale_workflow_transitions(
                _config, db, _config.stale_analysis_seconds
            )
            if transitions_recovered:
                logger.warning(
                    "Recuperadas %s transiciones obsoletas (archivando/descartando)",
                    transitions_recovered,
                )
            db_cleaned, orphans = purge_all_stale_process_data(_config, db)
            repaired = repair_processes_missing_data(_config, db)
            archived = repair_archived_processes(_config, db)
            discarded = repair_discarded_processes(_config, db)
            if (
                db_cleaned
                or orphans
                or repaired
                or archived
                or discarded
                or downloads_recovered
                or transitions_recovered
            ):
                db.commit()
                logger.info(
                    "Limpieza data/procesos: %s metadato(s) obsoleto(s), %s huérfana(s), "
                    "%s inconsistente(s), %s archivado(s) reparado(s), %s descartado(s) reparado(s)",
                    db_cleaned,
                    orphans,
                    repaired,
                    archived,
                    discarded,
                )
        finally:
            db.close()
        cleanup_task = asyncio.create_task(periodic_session_cleanup())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

    app = FastAPI(title="SEACE Monitor", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["can_open_seace"] = can_open_seace
    templates.env.globals["seace_view_path"] = seace_view_path
    templates.env.globals["InterestStatus"] = InterestStatus
    templates.env.filters["urlquote_path"] = lambda value: quote(str(value), safe="/")

    def render(request: Request, name: str, *, db: Session | None = None, **ctx):
        ctx["request"] = request
        ctx["active_page"] = ctx.get("active_page", "")
        badge_session = db
        own_session = False
        if badge_session is None:
            badge_session = session_factory()
            own_session = True
        try:
            ctx.setdefault("nav_badges", watchlist_nav_badges(badge_session))
        finally:
            if own_session:
                badge_session.close()
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, db: Session = Depends(get_db)):
        counts = {
            s.value: db.query(Process).filter(Process.status == s).count()
            for s in ProcessStatus
        }
        entities = db.query(Entity).filter(Entity.activa.is_(True)).count()
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
            db=db,
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
        valid_estado_values = {s.value for s in workflow_statuses}
        if estado:
            if estado not in valid_estado_values:
                raise HTTPException(
                    400,
                    "Estado no válido para publicaciones (publicada o descargando)",
                )
            q = q.filter(Process.status == ProcessStatus(estado))
        if entidad:
            q = q.filter(
                Process.entity_id
                == db.query(Entity.id).filter(Entity.ruc == entidad).scalar_subquery()
            )
        if objeto:
            q = q.filter(Process.objeto == objeto)
        sort_col = normalize_sort_for_columns(sort, PUBLICACIONES_SORT_COLUMNS)
        sort_dir = normalize_dir(dir, sort_col)
        rows = q.all()
        processes = sort_process_list_views(
            build_process_list_views(rows), sort_col, sort_dir
        )
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
            db=db,
            active_page="publicaciones",
            processes=processes,
            entities=entities,
            objetos=objetos,
            filtro_estado=filtro_estado,
            filtro_entidad=filtro_entidad,
            filtro_objeto=filtro_objeto,
            sort=sort_col,
            sort_dir=sort_dir,
            sort_columns=PUBLICACIONES_SORT_COLUMNS,
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

    def _workflow_list_redirect(
        path: str,
        *,
        sort: str = "",
        dir: str = "",
        scroll: str = "",
    ) -> RedirectResponse:
        return RedirectResponse(
            workflow_list_query(path, sort=sort, dir=dir, scroll=scroll),
            status_code=303,
        )

    def _descartados_redirect(estado: str = "") -> RedirectResponse:
        if estado in ("descartada", "autorejected"):
            return RedirectResponse(f"/descartados?estado={estado}", status_code=303)
        return RedirectResponse("/descartados", status_code=303)

    def _safe_workflow_path(path: str) -> str:
        allowed = {"/publicaciones", "/descargados", "/analizados", "/descartados", "/archivados"}
        return path if path in allowed else "/analizados"

    @app.post("/processes/{process_id}/interest")
    def cambiar_interes(
        process_id: int,
        db: Session = Depends(get_db),
        interest_status: str = Form(...),
        return_to: str = Form("/analizados"),
        sort: str = Form(""),
        dir: str = Form(""),
        scroll: str = Form(""),
    ):
        proc = get_process_or_404(db, process_id)
        try:
            proc.interest_status = InterestStatus(interest_status)
        except ValueError as exc:
            raise HTTPException(400, "Estado de interés inválido") from exc
        return _workflow_list_redirect(
            _safe_workflow_path(return_to), sort=sort, dir=dir, scroll=scroll
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
        proc = get_process_or_404(db, process_id)
        if proc.status == ProcessStatus.descartada:
            return _filter_redirect(
                entidad=entidad, objeto=objeto, sort=sort, dir=dir, scroll=scroll
            )
        if proc.status == ProcessStatus.descargando:
            raise HTTPException(409, "Descarga en curso")
        if proc.status != ProcessStatus.publicada:
            raise HTTPException(
                400, "Solo procesos publicados pueden descartarse desde Publicaciones"
            )
        proc.status = ProcessStatus.descartada
        return _filter_redirect(
            entidad=entidad, objeto=objeto, sort=sort, dir=dir, scroll=scroll
        )

    @app.get("/descartados", response_class=HTMLResponse)
    def descartados(
        request: Request,
        db: Session = Depends(get_db),
        estado: str = "",
    ):
        status_filter = {
            "descartada": [ProcessStatus.descartada],
            "autorejected": [ProcessStatus.autorejected],
        }.get(estado, [ProcessStatus.descartada, ProcessStatus.autorejected])
        rows = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.status.in_(status_filter))
            .order_by(Process.updated_at.desc())
            .all()
        )
        return render(
            request,
            "descartados.html",
            db=db,
            active_page="descartados",
            processes=rows,
            estado=estado if estado in ("descartada", "autorejected") else "",
        )

    @app.post("/descartados/{process_id}/restaurar")
    def restaurar(
        process_id: int,
        db: Session = Depends(get_db),
        estado: str = Form(""),
    ):
        proc = get_process_or_404(db, process_id, with_analysis=True)
        was_autorejected = proc.status == ProcessStatus.autorejected
        new_status = resolve_restore_status(_config, proc)
        if new_status == ProcessStatus.publicada:
            clear_process_download_metadata(proc)
            delete_process_analysis(db, proc)
        if was_autorejected:
            proc.auto_reject_exempt = True
            proc.auto_reject_reason = None
        proc.status = new_status
        return _descartados_redirect(estado)

    @app.post("/descartados/{process_id}/descartar")
    def descartar_autorejected(
        process_id: int,
        db: Session = Depends(get_db),
        estado: str = Form(""),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status == ProcessStatus.descartada:
            return _descartados_redirect(estado)
        if proc.status != ProcessStatus.autorejected:
            raise HTTPException(400, "Solo autorejected puede descartarse desde Descartados")
        proc.status = ProcessStatus.descartada
        proc.auto_reject_reason = None
        return _descartados_redirect(estado)

    @app.get("/archivados", response_class=HTMLResponse)
    def archivados(request: Request, db: Session = Depends(get_db)):
        rows = (
            db.query(Process)
            .options(joinedload(Process.entity), joinedload(Process.analysis))
            .filter(Process.status == ProcessStatus.archivada)
            .order_by(Process.updated_at.desc())
            .all()
        )
        return render(
            request,
            "archivados.html",
            db=db,
            active_page="archivados",
            processes=rows,
        )

    @app.post("/archivados/{process_id}/restaurar")
    def restaurar_archivado(process_id: int, db: Session = Depends(get_db)):
        proc = get_process_or_404(db, process_id, with_analysis=True)
        if proc.status != ProcessStatus.archivada:
            raise HTTPException(400, "Solo procesos archivados")
        restore_archived_process(_config, proc, db)
        return RedirectResponse("/archivados", status_code=303)

    @app.get("/archivados/{process_id}", response_class=HTMLResponse)
    def archivado_detalle(request: Request, process_id: int, db: Session = Depends(get_db)):
        proc = get_process_or_404(
            db, process_id, with_entity=True, with_analysis=True
        )
        if proc.status != ProcessStatus.archivada:
            raise HTTPException(404)
        ctx = _build_analisis_detail_context(proc, mark_read=False)
        return render(
            request,
            "analizado_detalle.html",
            db=db,
            active_page="archivados",
            back_href="/archivados",
            doc_route="analizados",
            is_archived=True,
            ProcessStatus=ProcessStatus,
            **ctx,
        )

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
        proc = get_process_or_404(db, process_id)
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

        process_id = begin_download_transition(db, proc)
        schedule_download(background_tasks, _config, process_id)
        return _filter_redirect(
            entidad=entidad,
            objeto=objeto,
            sort=sort,
            dir=dir,
            scroll=scroll,
        )

    @app.get("/descargados", response_class=HTMLResponse)
    def descargados(
        request: Request,
        db: Session = Depends(get_db),
        sort: str | None = None,
        dir: str | None = None,
    ):
        return render_workflow_list(
            request,
            db,
            render,
            template="descargados.html",
            active_page="descargados",
            statuses=[ProcessStatus.descargada, ProcessStatus.descartando],
            rank_attr="list_rank_descargados",
            sort=sort,
            dir=dir,
        )

    @app.get("/descargados/{process_id}", response_class=HTMLResponse)
    def descargado_detalle(request: Request, process_id: int, db: Session = Depends(get_db)):
        proc = get_process_or_404(
            db, process_id, with_entity=True, with_analysis=True
        )
        if proc.status == ProcessStatus.descartando:
            return RedirectResponse("/descargados", status_code=303)
        if proc.status == ProcessStatus.descartada:
            return RedirectResponse("/descargados", status_code=303)
        if proc.status not in (ProcessStatus.descargada, ProcessStatus.analizada):
            if proc.status == ProcessStatus.publicada:
                return RedirectResponse(f"/publicaciones", status_code=303)
            raise HTTPException(404)
        prev_cron = proc.watch_cronograma_prev_json if proc.watch_unread else None
        prev_docs = proc.watch_documentos_prev_json if proc.watch_unread else None
        if proc.watch_unread:
            mark_watchlist_read(proc)
        checked_paths = None
        if proc.data_dir and proc.analysis and proc.analysis.status in ("running", "error"):
            checked_paths = load_analysis_selection(Path(proc.data_dir))
        documentos = build_document_tree(
            proc,
            checked_paths=checked_paths,
            prev_documentos_json=prev_docs,
        )
        cronograma = parse_cronograma(
            proc.cronograma_json, prev_cronograma_json=prev_cron
        )
        archivos_analizando = [
            leaf.nombre
            for leaf in flatten_selectable_leaves(documentos)
            if leaf.default_checked
        ]
        had_watch_updates = bool(prev_cron or prev_docs)
        return render(
            request,
            "descargado_detalle.html",
            db=db,
            active_page="descargados",
            process=proc,
            documentos=documentos,
            documento_count=count_document_nodes(documentos),
            archivos_analizando=archivos_analizando,
            cronograma=cronograma,
            had_watch_updates=had_watch_updates,
            watch_changelog=parse_watch_changelog(proc.watch_changelog_json),
            ProcessStatus=ProcessStatus,
        )

    @app.post("/descargados/{process_id}/analizar")
    async def analizar_descargado(
        request: Request,
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
    ):
        proc = get_process_or_404(db, process_id, with_analysis=True)
        if proc.status not in (ProcessStatus.descargada, ProcessStatus.analizada):
            raise HTTPException(400, "Solo procesos descargados pueden analizarse")
        if proc.analysis and proc.analysis.status == "running":
            if not is_stale_running_analysis(
                proc.analysis, _config.stale_analysis_seconds
            ):
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

        if proc.data_dir:
            save_analysis_selection(Path(proc.data_dir), selected)

        if proc.data_dir and proc.analysis and proc.analysis.status == "done":
            from ..analysis.analysis_history import archive_analysis_before_rerun

            archive_analysis_before_rerun(Path(proc.data_dir), proc.analysis)

        analysis = proc.analysis
        run_id = uuid.uuid4().hex
        prior_snapshot = None
        if analysis is not None and analysis.status == "done":
            prior_snapshot = AnalysisRunner._analysis_snapshot(analysis)
        if analysis is None:
            analysis = AnalysisResult(process_id=proc.id, status="running", run_id=run_id)
            db.add(analysis)
        else:
            AnalysisRunner._mark_analysis_running(analysis, run_id)
        if proc.status == ProcessStatus.analizada:
            proc.status = ProcessStatus.descargada
        db.commit()
        db.expunge_all()

        selected_paths = list(selected)

        def _job():
            session = session_factory()
            try:
                runner = AnalysisRunner(_config, session)
                runner.analyze(
                    process_id,
                    selected_paths,
                    run_id=run_id,
                    prior_snapshot=prior_snapshot,
                )
            except AnalysisBusyError as exc:
                logger.warning("Análisis concurrente bloqueado para proceso %s", process_id)
                abandon_stale_analysis_run(
                    session,
                    process_id,
                    run_id,
                    message=str(exc),
                )
            except Exception:
                logger.exception("Background analysis failed")
                abandon_stale_analysis_run(
                    session,
                    process_id,
                    run_id,
                    message="Análisis interrumpido inesperadamente. Reintenta.",
                )
            finally:
                session.close()

        background_tasks.add_task(_job)
        return RedirectResponse(
            f"/descargados/{process_id}?msg=analisis_iniciado",
            status_code=303,
        )

    def _discard_downloaded(
        db: Session,
        proc: Process,
        background_tasks: BackgroundTasks,
        *,
        redirect: str,
    ) -> RedirectResponse:
        if proc.status == ProcessStatus.descartando:
            return RedirectResponse(redirect, status_code=303)
        if proc.status != ProcessStatus.descargada:
            raise HTTPException(400, "Estado no válido para descartar")
        if proc.analysis and proc.analysis.status == "running":
            if not is_stale_running_analysis(
                proc.analysis, _config.stale_analysis_seconds
            ):
                raise HTTPException(409, "Análisis en curso")
        process_id, _ = begin_discard_transition(db, proc)
        schedule_status_transition(
            background_tasks,
            _config,
            process_id,
            expected_status=ProcessStatus.descartando,
            work=lambda session, p: discard_work(_config, session, p),
            rollback_status=ProcessStatus.descargada,
            log_label="Background discard",
        )
        return RedirectResponse(redirect, status_code=303)

    def _archive_analyzed(
        db: Session,
        proc: Process,
        background_tasks: BackgroundTasks,
        *,
        redirect: str,
    ) -> RedirectResponse:
        if proc.status == ProcessStatus.archivando:
            return RedirectResponse(redirect, status_code=303)
        if proc.status not in (ProcessStatus.analizada, ProcessStatus.portafolio):
            raise HTTPException(400, "Estado no válido para archivar")
        if proc.analysis and proc.analysis.status == "running":
            if not is_stale_running_analysis(
                proc.analysis, _config.stale_analysis_seconds
            ):
                raise HTTPException(409, "Análisis en curso")
        process_id, restore_status = begin_archive_transition(db, proc)
        schedule_status_transition(
            background_tasks,
            _config,
            process_id,
            expected_status=ProcessStatus.archivando,
            work=lambda session, p: archive_work(_config, session, p),
            rollback_status=restore_status,
            log_label="Background archive",
        )
        return RedirectResponse(redirect, status_code=303)

    @app.post("/descargados/{process_id}/descartar")
    def descartar_descargado(
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        scroll: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
    ):
        proc = get_process_or_404(db, process_id, with_analysis=True)
        redirect = workflow_list_query(
            "/descargados",
            sort=sort,
            dir=dir,
            scroll=scroll.strip() if scroll.strip().isdigit() else "",
        )
        return _discard_downloaded(
            db, proc, background_tasks, redirect=redirect
        )

    @app.post("/analizados/{process_id}/archivar")
    def archivar_analizado(
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        scroll: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
    ):
        proc = get_process_or_404(db, process_id, with_analysis=True)
        redirect = workflow_list_query(
            "/analizados",
            sort=sort,
            dir=dir,
            scroll=scroll.strip() if scroll.strip().isdigit() else "",
        )
        return _archive_analyzed(
            db, proc, background_tasks, redirect=redirect
        )

    @app.get("/seace/open/{process_id}")
    def seace_open(process_id: int, request: Request, db: Session = Depends(get_db)):
        proc = get_process_or_404(db, process_id, with_entity=True)
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
    def descartar_analizado(
        process_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        scroll: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
    ):
        """Compat: redirige al flujo de archivar."""
        return archivar_analizado(
            process_id, background_tasks, db, scroll=scroll, sort=sort, dir=dir
        )

    @app.post("/analizados/{process_id}/estado")
    def cambiar_estado_analizados(
        process_id: int,
        estado: str = Form(...),
        db: Session = Depends(get_db),
        scroll: str = Form(""),
        sort: str = Form(""),
        dir: str = Form(""),
    ):
        proc = get_process_or_404(db, process_id)
        if estado not in (ProcessStatus.analizada.value, ProcessStatus.portafolio.value):
            raise HTTPException(400, "Estado inválido")
        if proc.status not in (ProcessStatus.analizada, ProcessStatus.portafolio):
            raise HTTPException(400, "Solo procesos analizados o en portafolio")
        proc.status = ProcessStatus(estado)
        db.commit()
        return _workflow_list_redirect(
            "/analizados",
            sort=sort,
            dir=dir,
            scroll=scroll.strip() if scroll.strip().isdigit() else "",
        )

    @app.get("/analizados", response_class=HTMLResponse)
    def analizados(
        request: Request,
        db: Session = Depends(get_db),
        sort: str | None = None,
        dir: str | None = None,
    ):
        return render_workflow_list(
            request,
            db,
            render,
            template="analizados.html",
            active_page="analizados",
            statuses=[
                ProcessStatus.analizada,
                ProcessStatus.portafolio,
                ProcessStatus.archivando,
            ],
            rank_attr="list_rank_analizados",
            sort=sort,
            dir=dir,
            extra_context={"ProcessStatus": ProcessStatus},
        )

    @app.get("/analizados/{process_id}", response_class=HTMLResponse)
    def analizado_detalle(request: Request, process_id: int, db: Session = Depends(get_db)):
        proc = get_process_or_404(
            db, process_id, with_entity=True, with_analysis=True
        )
        if proc.status == ProcessStatus.archivando:
            return RedirectResponse("/analizados", status_code=303)
        if proc.status == ProcessStatus.archivada:
            return RedirectResponse(f"/archivados/{process_id}", status_code=303)
        if proc.status not in (
            ProcessStatus.analizada,
            ProcessStatus.portafolio,
        ):
            raise HTTPException(404)
        ctx = _build_analisis_detail_context(proc, mark_read=True)
        return render(
            request,
            "analizado_detalle.html",
            db=db,
            active_page="analizados",
            back_href="/analizados",
            doc_route="analizados",
            is_archived=False,
            ProcessStatus=ProcessStatus,
            **ctx,
        )

    @app.get("/api/processes/{process_id}/workflow")
    def api_process_workflow(process_id: int, db: Session = Depends(get_db)):
        proc = get_process_or_404(db, process_id, with_analysis=True)
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

    register_process_document_routes(app, "descargados", get_db)
    register_process_document_routes(app, "analizados", get_db)
    register_settings_routes(app, _config, render, get_db)
    register_autoreject_settings_routes(app, _config, render, get_db)
    register_analysis_chat_routes(app, _config, get_db)

    return app
