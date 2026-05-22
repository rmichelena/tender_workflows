"""Renderizado seguro de Markdown para vistas web."""

from __future__ import annotations

import re

import markdown


_CRONOGRAMA_MARKER = "## Cronograma (ficha SEACE"
_PREAMBLE_RE = re.compile(
    r"^(?:\s*(?:claro|aquí tienes|a continuación|como solicitaste|a continuación te presento)"
    r"[^\n]*\n)+",
    re.IGNORECASE | re.MULTILINE,
)


def strip_llm_preamble(text: str) -> str:
    """Quita saludos meta típicos del LLM antes del contenido útil."""
    cleaned = text.lstrip()
    cleaned = _PREAMBLE_RE.sub("", cleaned, count=1)
    while cleaned.startswith(("***", "---", "___")):
        cleaned = cleaned.split("\n", 1)[-1].lstrip()
    return cleaned


def strip_appended_cronograma(text: str) -> str:
    """Quita la sección de cronograma añadida al .md (se muestra aparte en la UI)."""
    idx = text.find(_CRONOGRAMA_MARKER)
    if idx >= 0:
        return text[:idx].rstrip()
    return text


def render_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )


def render_free_reader_summary(text: str) -> str:
    body = strip_appended_cronograma(strip_llm_preamble(text))
    return render_markdown(body)


def render_chat_reply(text: str) -> str:
    return render_free_reader_summary(text)
