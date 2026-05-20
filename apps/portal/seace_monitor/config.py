"""Carga de configuración YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DURATION_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|day|days?)$",
    re.I,
)


def parse_duration(value: str | int | float) -> int:
    """Convierte '6h', '30m', 300, etc. a segundos."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(f"Duración inválida: {value!r} (ej. 6h, 30m, 300)")
    amount = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("s"):
        return int(amount)
    if unit.startswith("m"):
        return int(amount * 60)
    if unit.startswith("h"):
        return int(amount * 3600)
    if unit.startswith("d"):
        return int(amount * 86400)
    raise ValueError(f"Unidad no reconocida en: {value!r}")


@dataclass
class TenderProcurementConfig:
    """Integración con repo tender_procurement (pasos 1.0–1.3 + eje 0)."""

    repo_path: Path | None = None
    # auto_leave: si hay candidatos de planos sin Gemini, marcar leave_for_ocr y continuar
    # stop: exit 23 detiene el análisis (comportamiento estricto del runner upstream)
    # gemini: reservado — requiere implementar visión (pendiente)
    planos_mode: str = "auto_leave"
    run_axis0: bool = True
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key_env: str = "GEMINI_API_KEY"


@dataclass
class AnalysisConfig:
    stage1_script: Path | None = None
    stage2_script: Path | None = None
    scripts_timeout_seconds: int = 3600
    tender: TenderProcurementConfig = field(default_factory=TenderProcurementConfig)


@dataclass
class AppConfig:
    poll_interval: str = "6h"
    anio: int = 2026
    entities_csv: Path = Path("entities.csv")
    database_url: str = "sqlite:///./data/seace.db"
    max_pages: int = 1
    rows_per_page: int = 15
    data_dir: Path = Path("./data")
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)

    @property
    def poll_interval_seconds(self) -> int:
        return parse_duration(self.poll_interval)

    @classmethod
    def load(cls, path: Path | str = "config.yaml") -> AppConfig:
        import os

        p = Path(path)
        with open(p, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        if os.environ.get("DATABASE_URL"):
            raw["database_url"] = os.environ["DATABASE_URL"]

        analysis_raw = raw.pop("analysis", {}) or {}
        tender_raw = analysis_raw.pop("tender_procurement", {}) or {}
        tender = TenderProcurementConfig(
            repo_path=_optional_path(tender_raw.get("repo_path")),
            planos_mode=str(tender_raw.get("planos_mode", "auto_leave")),
            run_axis0=bool(tender_raw.get("run_axis0", True)),
            gemini_model=str(tender_raw.get("gemini_model", "gemini-2.5-flash")),
            gemini_api_key_env=str(tender_raw.get("gemini_api_key_env", "GEMINI_API_KEY")),
        )
        analysis = AnalysisConfig(
            stage1_script=_optional_path(analysis_raw.get("stage1_script")),
            stage2_script=_optional_path(analysis_raw.get("stage2_script")),
            scripts_timeout_seconds=int(
                analysis_raw.get("scripts_timeout_seconds", 3600)
            ),
            tender=tender,
        )

        return cls(
            poll_interval=str(raw.get("poll_interval", raw.get("poll_interval_seconds", "6h"))),
            anio=int(raw.get("anio", 2026)),
            entities_csv=Path(raw.get("entities_csv", "entities.csv")),
            database_url=str(
                raw.get(
                    "database_url",
                    "sqlite:///./data/seace.db",
                )
            ),
            max_pages=int(raw.get("max_pages", 1)),
            rows_per_page=int(raw.get("rows_per_page", 15)),
            data_dir=Path(raw.get("data_dir", "./data")),
            analysis=analysis,
        )


def _optional_path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value))
