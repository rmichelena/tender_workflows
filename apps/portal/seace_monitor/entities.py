"""Carga de entidades desde CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EntityRow:
    ruc: str
    nombre: str


def resolve_entities_csv(path: Path | str | None = None) -> Path:
    """Resuelve la ruta del CSV de entidades (repo root, entities.csv, entidades.csv)."""
    from .tender_repo import resolve_tender_repo_root

    configured = Path(path or "entities.csv")
    candidates: list[Path] = []
    if configured.is_absolute():
        candidates.append(configured)
    else:
        candidates.append(configured)
        try:
            root = resolve_tender_repo_root()
            candidates.extend(
                [
                    root / configured,
                    root / "entities.csv",
                    root / "entidades.csv",
                ]
            )
        except RuntimeError:
            candidates.append(Path("entidades.csv"))

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved

    return configured.resolve() if configured.is_absolute() else configured


def load_entities_csv(path: Path) -> list[EntityRow]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo de entidades: {path}")

    rows: list[EntityRow] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        peek = f.read(512)
        f.seek(0)
        if "ruc" in peek.lower():
            reader = csv.DictReader(f)
            for row in reader:
                ruc = _pick(row, "ruc", "RUC", "ruc_entidad")
                nombre = _pick(row, "nombre", "Nombre", "entidad", "entidades", "name")
                if ruc:
                    rows.append(EntityRow(ruc=_normalize_ruc(ruc), nombre=nombre or ruc))
        else:
            reader = csv.reader(f)
            for line in reader:
                if not line or not line[0].strip():
                    continue
                ruc = _normalize_ruc(line[0].strip().strip('"'))
                nombre = line[1].strip().strip('"') if len(line) > 1 else ruc
                rows.append(EntityRow(ruc=ruc, nombre=nombre))

    if not rows:
        raise ValueError(f"Sin entidades en {path}")
    return rows


def _pick(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and row[k]:
            return row[k].strip()
    return ""


def _normalize_ruc(ruc: str) -> str:
    return "".join(c for c in ruc if c.isdigit())
