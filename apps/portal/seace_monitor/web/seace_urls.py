"""URLs y helpers de source para la UI (SEACE y ADP)."""

from __future__ import annotations

from .seace_proxy import seace_view_path
from .seace_view import can_open_seace, can_open_source, source_button_label, source_view_url

__all__ = ["can_open_seace", "can_open_source", "seace_view_path", "source_button_label", "source_view_url"]
