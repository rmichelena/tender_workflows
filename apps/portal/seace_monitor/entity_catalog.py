"""Catálogo oficial de entidades contratantes (OSCE, pipe-separated)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Entity
from .http_util import requests_proxies
from .tenant_paths import tenant_settings_dir

logger = logging.getLogger(__name__)

OFFICIAL_ENTITIES_URL = (
    "https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/"
    "entidadescontratantes/entidades_contratantes.csv"
)


@dataclass(frozen=True)
class OfficialEntityRow:
    ruc: str
    nombre: str
    departamento: str
    provincia: str
    distrito: str
    codigo_siaf: str
    codconsucode: str
    estado: str
    ultima_actualizacion: str


@dataclass
class EntityCatalogSyncResult:
    changed: bool
    added: int
    updated: int
    skipped_inactivo: int
    content_hash: str


def _catalog_state_path(config: AppConfig) -> Path:
    return tenant_settings_dir(config) / "entity_catalog_state.json"


def _normalize_ruc(ruc: str) -> str:
    return "".join(c for c in ruc if c.isdigit())


def parse_official_entities(text: str) -> list[OfficialEntityRow]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip().upper() for h in lines[0].split("|")]
    idx = {name: i for i, name in enumerate(header)}
    required = ("RUC", "NOMBRE_DE_ENTIDAD", "ESTADO")
    for key in required:
        if key not in idx:
            raise ValueError(f"Columna requerida ausente en catálogo OSCE: {key}")

    rows: list[OfficialEntityRow] = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < len(header):
            continue
        ruc = _normalize_ruc(parts[idx["RUC"]].strip())
        if not ruc:
            continue
        rows.append(
            OfficialEntityRow(
                ruc=ruc,
                nombre=parts[idx["NOMBRE_DE_ENTIDAD"]].strip(),
                departamento=_field(parts, idx, "DEPARTAMENTO"),
                provincia=_field(parts, idx, "PROVINCIA"),
                distrito=_field(parts, idx, "DISTRITO"),
                codigo_siaf=_field(parts, idx, "CODIGO_SIAF"),
                codconsucode=_field(parts, idx, "CODCONSUCODE"),
                estado=parts[idx["ESTADO"]].strip(),
                ultima_actualizacion=_field(parts, idx, "ULTIMAACTUALIZACION"),
            )
        )
    return rows


def _field(parts: list[str], idx: dict[str, int], key: str) -> str:
    i = idx.get(key)
    if i is None or i >= len(parts):
        return ""
    return parts[i].strip()


def fetch_official_entities(config: AppConfig) -> tuple[str, list[OfficialEntityRow]]:
    proxies = requests_proxies(config.http_proxy)
    r = requests.get(OFFICIAL_ENTITIES_URL, timeout=120, proxies=proxies)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    text = r.text
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), parse_official_entities(text)


def sync_official_entities(
    session: Session, rows: list[OfficialEntityRow]
) -> EntityCatalogSyncResult:
    """Importa/actualiza entidades Activo; no borra; no agrega Inactivo nuevas."""
    content_hash = hashlib.sha256(
        "\n".join(f"{r.ruc}|{r.estado}|{r.nombre}" for r in rows).encode()
    ).hexdigest()
    by_ruc = {e.ruc: e for e in session.query(Entity).all()}
    added = updated = skipped_inactivo = 0

    for row in rows:
        estado_norm = row.estado.strip().lower()
        existing = by_ruc.get(row.ruc)
        if existing is None and estado_norm != "activo":
            skipped_inactivo += 1
            continue
        if existing is None:
            ent = Entity(ruc=row.ruc, nombre=row.nombre, activa=False)
            session.add(ent)
            by_ruc[row.ruc] = ent
            added += 1
        else:
            updated += 1
        ent = by_ruc[row.ruc]
        ent.nombre = row.nombre or ent.nombre
        ent.estado_osce = row.estado
        ent.departamento = row.departamento or None
        ent.provincia = row.provincia or None
        ent.distrito = row.distrito or None
        ent.codigo_siaf = row.codigo_siaf or None
        ent.codconsucode = row.codconsucode or None
        ent.osce_ultima_actualizacion = row.ultima_actualizacion or None

    session.flush()
    return EntityCatalogSyncResult(
        changed=True,
        added=added,
        updated=updated,
        skipped_inactivo=skipped_inactivo,
        content_hash=content_hash,
    )


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(path: Path, *, content_hash: str, added: int, updated: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "content_hash": content_hash,
        "last_sync_at": datetime.now(timezone.utc).isoformat(),
        "added": added,
        "updated": updated,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sync_entity_catalog_if_changed(
    session: Session, config: AppConfig, *, force: bool = False
) -> EntityCatalogSyncResult | None:
    """Descarga catálogo OSCE y aplica cambios si el hash difiere."""
    state_path = _catalog_state_path(config)
    prev = _load_state(state_path)
    digest, rows = fetch_official_entities(config)
    if not force and prev.get("content_hash") == digest:
        logger.info("Catálogo OSCE sin cambios (hash %s…)", digest[:12])
        return None
    result = sync_official_entities(session, rows)
    result.content_hash = digest
    _save_state(
        state_path,
        content_hash=digest,
        added=result.added,
        updated=result.updated,
    )
    session.commit()
    logger.info(
        "Catálogo OSCE sincronizado: +%s actualizadas %s omitidas Inactivo nuevas",
        result.added,
        result.updated,
        result.skipped_inactivo,
    )
    return result


def ensure_entity_catalog(session: Session, config: AppConfig) -> None:
    """Bootstrap: sincroniza catálogo OSCE si cambió el CSV oficial."""
    tenant_settings_dir(config).mkdir(parents=True, exist_ok=True)
    try:
        sync_entity_catalog_if_changed(session, config)
    except Exception:
        logger.exception("No se pudo sincronizar catálogo OSCE al inicio")
    session.commit()
