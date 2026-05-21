"""URLs y helpers SEACE para la UI."""

from __future__ import annotations

from .seace_proxy import seace_view_path
from .seace_view import can_open_seace

__all__ = ["can_open_seace", "seace_view_path"]
