"""Modelos SQLAlchemy."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProcessStatus(str, enum.Enum):
    publicada = "publicada"
    descargando = "descargando"
    descargada = "descargada"
    descartando = "descartando"
    analizada = "analizada"
    portafolio = "portafolio"
    autorejected = "autorejected"
    archivando = "archivando"
    descartada = "descartada"
    archivada = "archivada"


class InterestStatus(str, enum.Enum):
    none = "none"
    watching = "watching"
    candidate = "candidate"
    opportunity = "opportunity"
    rejected = "rejected"


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(512))
    activa: Mapped[bool] = mapped_column(default=False, index=True)
    estado_osce: Mapped[str | None] = mapped_column(String(32))
    departamento: Mapped[str | None] = mapped_column(String(128))
    provincia: Mapped[str | None] = mapped_column(String(128))
    distrito: Mapped[str | None] = mapped_column(String(128))
    codigo_siaf: Mapped[str | None] = mapped_column(String(32))
    codconsucode: Mapped[str | None] = mapped_column(String(32))
    osce_ultima_actualizacion: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    processes: Mapped[list[Process]] = relationship(back_populates="entity")


class Process(Base):
    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint("entity_id", "nid_proceso", name="uq_entity_nid_proceso"),
        Index("ix_processes_status_entity", "status", "entity_id"),
        Index("ix_processes_status_objeto", "status", "objeto"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="RESTRICT"), index=True
    )
    anio: Mapped[int] = mapped_column(Integer, index=True)

    source: Mapped[str] = mapped_column(String(32), default="seace", index=True)
    source_ref: Mapped[str | None] = mapped_column(
        String(256),
        default=lambda context: context.get_current_parameters().get("nid_proceso"),
        index=True,
    )
    workflow_profile: Mapped[str] = mapped_column(
        String(64), default="public_tender", index=True
    )
    interest_status: Mapped[InterestStatus] = mapped_column(
        Enum(InterestStatus, native_enum=False), default=InterestStatus.none, index=True
    )

    nid_proceso: Mapped[str] = mapped_column(String(32), index=True)
    nid_convocatoria: Mapped[str | None] = mapped_column(Text)
    nid_sistema: Mapped[str | None] = mapped_column(String(8))
    link_id: Mapped[str | None] = mapped_column(String(128))
    ntipo: Mapped[str | None] = mapped_column(String(8))
    ficha_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[ProcessStatus] = mapped_column(
        Enum(ProcessStatus, native_enum=False), default=ProcessStatus.publicada, index=True
    )

    # Columnas del listado SEACE
    numero: Mapped[str | None] = mapped_column(String(16))
    fecha_publicacion: Mapped[str | None] = mapped_column(String(32))
    nomenclatura: Mapped[str] = mapped_column(String(256), index=True)
    reiniciado_desde: Mapped[str | None] = mapped_column(String(256))
    objeto: Mapped[str | None] = mapped_column(String(256), index=True)
    descripcion: Mapped[str | None] = mapped_column(Text)
    cuantia: Mapped[str | None] = mapped_column(String(64))
    moneda: Mapped[str | None] = mapped_column(String(64))
    version_seace: Mapped[str | None] = mapped_column(String(8))

    # Del cronograma (columnas extra en la UI)
    fecha_consultas: Mapped[str | None] = mapped_column(String(64))
    fecha_presentacion: Mapped[str | None] = mapped_column(String(64))
    cronograma_json: Mapped[str | None] = mapped_column(Text)
    documentos_json: Mapped[str | None] = mapped_column(Text)
    ficha_url: Mapped[str | None] = mapped_column(String(512))

    list_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    data_dir: Mapped[str | None] = mapped_column(String(512))
    auto_reject_reason: Mapped[str | None] = mapped_column(Text)
    auto_reject_exempt: Mapped[bool] = mapped_column(default=False, index=True)

    watch_unread: Mapped[bool] = mapped_column(default=False, index=True)
    watch_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    watch_cronograma_prev_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_documentos_prev_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_changelog_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    list_rank_descargados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_rank_analizados: Mapped[int | None] = mapped_column(Integer, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    entity: Mapped[Entity] = relationship(back_populates="processes")
    analysis: Mapped[AnalysisResult | None] = relationship(
        back_populates="process", uselist=False
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    process_id: Mapped[int] = mapped_column(
        ForeignKey("processes.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|running|done|error
    run_id: Mapped[str | None] = mapped_column(String(36))
    error_message: Mapped[str | None] = mapped_column(Text)

    alcance: Mapped[str | None] = mapped_column(Text)
    incluye: Mapped[str | None] = mapped_column(Text)
    requisitos: Mapped[str | None] = mapped_column(Text)
    entregables: Mapped[str | None] = mapped_column(Text)
    equipos: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    process: Mapped[Process] = relationship(back_populates="analysis")
