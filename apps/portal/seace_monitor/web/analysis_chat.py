"""Rutas API para Q&A Gemini post-análisis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..analysis.gemini_session import (
    GeminiSession,
    load_session,
    public_chat_payload,
    send_chat_message,
)
from ..config import AppConfig
from ..db.models import Process, ProcessStatus
from .markdown_render import render_chat_reply, render_markdown


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


_CHAT_STATUSES = frozenset(
    {
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
        ProcessStatus.archivada,
    }
)


def _proc_dir(process: Process) -> Path:
    if not process.data_dir:
        raise HTTPException(400, "Proceso sin archivos locales")
    return Path(process.data_dir)


def _render_turn(turn: dict[str, str]) -> dict[str, str]:
    role = turn["role"]
    text = turn["text"]
    if role == "model":
        html = render_chat_reply(text)
    else:
        html = render_markdown(text)
    return {"role": role, "text": text, "html": html}


def api_chat_payload(session: GeminiSession | None) -> dict[str, Any]:
    payload = public_chat_payload(session)
    if payload.get("available"):
        payload["turns"] = [_render_turn(t) for t in payload["turns"]]
    return payload


def register_analysis_chat_routes(app, config: AppConfig, get_db):
    @app.get("/api/analizados/{process_id}/chat")
    def get_chat(process_id: int, db: Session = Depends(get_db)):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status not in _CHAT_STATUSES:
            raise HTTPException(400, "Chat solo disponible para procesos analizados")
        if not proc.analysis or proc.analysis.status != "done":
            raise HTTPException(400, "El análisis aún no está listo")
        session = load_session(_proc_dir(proc)) if proc.data_dir else None
        return api_chat_payload(session)

    @app.post("/api/analizados/{process_id}/chat")
    def post_chat(
        process_id: int,
        body: ChatMessageRequest,
        db: Session = Depends(get_db),
    ):
        proc = db.get(Process, process_id)
        if proc is None:
            raise HTTPException(404)
        if proc.status not in _CHAT_STATUSES:
            raise HTTPException(400, "Chat solo disponible para procesos analizados")
        if not proc.analysis or proc.analysis.status != "done":
            raise HTTPException(400, "El análisis aún no está listo")
        try:
            reply, session = send_chat_message(
                config, _proc_dir(proc), body.message.strip()
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "reply": reply,
            "reply_html": render_chat_reply(reply),
            **api_chat_payload(session),
        }
