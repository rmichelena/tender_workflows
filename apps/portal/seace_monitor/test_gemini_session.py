"""Tests para sesión Gemini (persistencia y payload público)."""

from __future__ import annotations

import json
from pathlib import Path

from .analysis.gemini_session import (
    GeminiSession,
    GeminiTurn,
    _resolve_stored_paths,
    clean_run_scoped_artifacts,
    load_session,
    public_chat_payload,
    save_session,
    session_path_for_proc_dir,
    _contents_for_question,
)


def test_gemini_session_roundtrip(tmp_path: Path):
    proc_dir = tmp_path / "proc"
    pdf = proc_dir / "documentos" / "bases.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 test")
    session = GeminiSession(
        model="gemini-2.5-flash",
        cache_name="cachedContents/abc123",
        cache_expire_at="2026-05-20T12:00:00+00:00",
        upload_paths=["documentos/bases.pdf"],
        source_paths=["documentos/bases.pdf"],
        bootstrap_user="Analiza estas bases.",
        bootstrap_model="## Resumen\n\nContenido inicial.",
        chat_turns=[
            GeminiTurn(role="user", text="¿Plazo?"),
            GeminiTurn(role="model", text="30 días."),
        ],
    )
    save_session(proc_dir, session)
    path = session_path_for_proc_dir(proc_dir)
    assert path.is_file()

    loaded = load_session(proc_dir)
    assert loaded is not None
    assert loaded.model == session.model
    assert loaded.cache_name == session.cache_name
    assert loaded.bootstrap_model == session.bootstrap_model
    assert len(loaded.chat_turns) == 2
    assert loaded.chat_turns[0].text == "¿Plazo?"


def test_resolve_stored_paths_relative_and_legacy(tmp_path: Path):
    proc_dir = tmp_path / "proc"
    pdf = proc_dir / "fast_analysis" / "merged.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    rel = _resolve_stored_paths(proc_dir, ["fast_analysis/merged.pdf"])
    assert rel == [pdf.resolve()]
    legacy = _resolve_stored_paths(proc_dir, [str(pdf)])
    assert legacy == [pdf.resolve()]


def test_public_chat_payload_available():
    session = GeminiSession(
        model="gemini-2.5-flash",
        cache_name="cachedContents/x",
        bootstrap_model="Resumen.",
        chat_turns=[GeminiTurn(role="user", text="Hola")],
    )
    payload = public_chat_payload(session)
    assert payload["available"] is True
    assert payload["turns"] == [{"role": "user", "text": "Hola"}]
    assert payload["message"] is None


def test_public_chat_payload_missing():
    payload = public_chat_payload(None)
    assert payload["available"] is False
    assert payload["turns"] == []
    assert "Sin sesión" in payload["message"]


def test_contents_for_question_includes_bootstrap_and_history():
    session = GeminiSession(
        model="gemini-2.5-flash",
        bootstrap_user="Prompt inicial",
        bootstrap_model="Respuesta inicial",
        chat_turns=[
            GeminiTurn(role="user", text="P1"),
            GeminiTurn(role="model", text="R1"),
        ],
    )
    contents = _contents_for_question(session, "P2")
    assert len(contents) == 5
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Prompt inicial"
    assert contents[-1].role == "user"
    assert contents[-1].parts[0].text == "P2"


def test_clean_run_scoped_artifacts_keeps_selected_files(tmp_path: Path):
    proc_dir = tmp_path / "proc"
    workspace = proc_dir / "fast_analysis"
    workspace.mkdir(parents=True)
    selection = workspace / "selected_files.json"
    selection.write_text('["a.pdf", "b.pdf"]', encoding="utf-8")
    stale = workspace / "old_run.txt"
    stale.write_text("x", encoding="utf-8")

    clean_run_scoped_artifacts(proc_dir)

    assert selection.is_file()
    assert json.loads(selection.read_text(encoding="utf-8")) == ["a.pdf", "b.pdf"]
    assert not stale.exists()

