"""Rutas compartidas de preview/descarga de documentos."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .detail_data import (
    download_filename_for_path,
    media_type_for_path,
    resolve_document_path,
)
from .process_queries import get_process_or_404


def register_process_document_routes(app: FastAPI, route_prefix: str, get_db) -> None:
    @app.get(f"/{route_prefix}/{{process_id}}/preview/{{filename:path}}")
    def preview_documento(
        process_id: int, filename: str, db: Session = Depends(get_db)
    ):
        proc = get_process_or_404(db, process_id)
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

    @app.get(f"/{route_prefix}/{{process_id}}/documentos/{{filename:path}}")
    def descargar_documento(
        process_id: int, filename: str, db: Session = Depends(get_db)
    ):
        proc = get_process_or_404(db, process_id)
        path = resolve_document_path(proc, filename)
        if path is None:
            raise HTTPException(404, "Documento no encontrado")
        return FileResponse(
            path,
            media_type=media_type_for_path(path),
            filename=download_filename_for_path(proc, path),
        )
