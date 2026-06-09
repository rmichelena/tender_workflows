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


class LifecyclePhase(str, enum.Enum):
    """Fase comercial del objeto, ortogonal a `status` (portal) y `stage` (A–D).

    El estudio de mercado no es un tipo de proceso separado, sino la fase previa
    del mismo item, que puede transicionar a `licitacion` sin duplicarse.
    """

    estudio_mercado = "estudio_mercado"
    licitacion = "licitacion"
    adjudicacion = "adjudicacion"
    ejecucion = "ejecucion"


def _default_source_ref(context) -> str:
    params = context.get_current_parameters()
    return str(params.get("source_ref") or params.get("nid_proceso") or "")


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

    processes: Mapped[list["FeedItem"]] = relationship(back_populates="entity")


class FeedItem(Base):
    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "entity_id",
            "source_ref",
            name="uq_process_source_identity",
        ),
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
        default=_default_source_ref,
        index=True,
    )
    workflow_profile: Mapped[str] = mapped_column(
        String(64), default="public_tender", index=True
    )
    interest_status: Mapped[InterestStatus] = mapped_column(
        Enum(InterestStatus, native_enum=False), default=InterestStatus.none, index=True
    )
    lifecycle_phase: Mapped[LifecyclePhase] = mapped_column(
        Enum(LifecyclePhase, native_enum=False),
        default=LifecyclePhase.licitacion,
        index=True,
    )

    nid_proceso: Mapped[str | None] = mapped_column(String(32), index=True)
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

    # Promoción feed→pipeline (0.3d): latch de un solo sentido. NULL = feed puro
    # (descubrimiento ruidoso, purgable); set = trabajo curado (descarga/análisis/interés).
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    entity: Mapped[Entity] = relationship(back_populates="processes")
    analysis: Mapped[AnalysisResult | None] = relationship(
        back_populates="process", uselist=False
    )


class PipelineItem(Base):
    """Contexto de trabajo curado (pipeline), privado por tenant.

    Se crea por **promoción** desde un `FeedItem` (hoy `Process`) ante una acción
    positiva del usuario (descargar/analizar/marcar interés). El snapshot del feed
    se copia **sin foreign key** — el feed puede purgarse sin romper el pipeline.
    """
    __tablename__ = "pipeline_items"
    __table_args__ = (
        Index("ix_pipeline_items_status_entity", "status", "entity_id"),
        Index("ix_pipeline_items_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), default="default", index=True
    )

    # --- Origen (snapshot del feed, SIN FK) ---
    origin_feed_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    origin_source: Mapped[str] = mapped_column(String(32), default="seace")
    origin_source_ref: Mapped[str | None] = mapped_column(String(256))

    # Alias de compatibilidad (templates/code acceden a .source)
    @property
    def source(self) -> str:
        return self.origin_source

    @source.setter
    def source(self, value: str) -> None:
        self.origin_source = value

    @property
    def source_ref(self) -> str | None:
        return self.origin_source_ref

    @source_ref.setter
    def source_ref(self, value: str | None) -> None:
        self.origin_source_ref = value

    # ID del Process original para rutas que operan sobre la tabla processes
    @property
    def process_id(self) -> int | None:
        """ID del FeedItem/Process original — usado por rutas de acciones."""
        return self.origin_feed_id

    # --- Clasificación ---
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="RESTRICT"), index=True
    )
    anio: Mapped[int] = mapped_column(Integer, index=True)
    workflow_profile: Mapped[str] = mapped_column(
        String(64), default="public_tender", index=True
    )
    interest_status: Mapped[InterestStatus] = mapped_column(
        Enum(InterestStatus, native_enum=False), default=InterestStatus.none, index=True
    )
    lifecycle_phase: Mapped[LifecyclePhase] = mapped_column(
        Enum(LifecyclePhase, native_enum=False),
        default=LifecyclePhase.licitacion,
        index=True,
    )

    # --- Estado operativo del portal ---
    status: Mapped[ProcessStatus] = mapped_column(
        Enum(ProcessStatus, native_enum=False), default=ProcessStatus.publicada, index=True
    )

    # --- Campos SEACE / fuente (denormalizados) ---
    nid_proceso: Mapped[str | None] = mapped_column(String(32), index=True)
    nid_convocatoria: Mapped[str | None] = mapped_column(Text)
    nid_sistema: Mapped[str | None] = mapped_column(String(8))
    link_id: Mapped[str | None] = mapped_column(String(128))
    ntipo: Mapped[str | None] = mapped_column(String(8))
    ficha_id: Mapped[str | None] = mapped_column(String(36))
    numero: Mapped[str | None] = mapped_column(String(16))
    fecha_publicacion: Mapped[str | None] = mapped_column(String(32))
    nomenclatura: Mapped[str] = mapped_column(String(256), index=True)
    reiniciado_desde: Mapped[str | None] = mapped_column(String(256))
    objeto: Mapped[str | None] = mapped_column(String(256), index=True)
    descripcion: Mapped[str | None] = mapped_column(Text)
    cuantia: Mapped[str | None] = mapped_column(String(64))
    moneda: Mapped[str | None] = mapped_column(String(64))
    version_seace: Mapped[str | None] = mapped_column(String(8))

    # --- Cronograma y documentos ---
    fecha_consultas: Mapped[str | None] = mapped_column(String(64))
    fecha_presentacion: Mapped[str | None] = mapped_column(String(64))
    cronograma_json: Mapped[str | None] = mapped_column(Text)
    documentos_json: Mapped[str | None] = mapped_column(Text)
    ficha_url: Mapped[str | None] = mapped_column(String(512))

    # --- Contenido y almacenamiento ---
    content_hash: Mapped[str | None] = mapped_column(String(64))
    data_dir: Mapped[str | None] = mapped_column(String(512))

    # --- Watchlist ---
    watch_unread: Mapped[bool] = mapped_column(default=False, index=True)
    watch_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    watch_cronograma_prev_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_documentos_prev_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_changelog_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- List ordering ---
    list_rank_descargados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_rank_analizados: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Timestamps ---
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    entity: Mapped[Entity] = relationship()
    analysis: Mapped[AnalysisResult | None] = relationship(
        back_populates="pipeline_item", uselist=False
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    process_id: Mapped[int] = mapped_column(
        ForeignKey("processes.id"), unique=True, index=True
    )
    pipeline_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_items.id"), unique=True, nullable=True, index=True
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

    process: Mapped["FeedItem"] = relationship(back_populates="analysis")
    pipeline_item: Mapped[PipelineItem | None] = relationship(back_populates="analysis")


class TenantFeedDecision(Base):
    """Overlay por tenant sobre el feed: decisiones de autoreject/exempt.

    Paso 0.3b del split feed/pipeline (`docs/INGEST_CONTRACT.md` §4/§9). El feed es
    compartido (sin `tenant_id`); las decisiones de cada tenant viven aquí. Hoy el feed
    se materializa sobre `processes`, así que `feed_item_id` referencia `processes.id`
    **sin foreign key** (para no acoplar al futuro purgado/separación del feed). Una
    decisión por `(tenant_id, feed_item_id)`; `exempt` supersede a `autorejected`.
    """

    __tablename__ = "tenant_feed_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "feed_item_id", name="uq_tenant_feed_decision"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    feed_item_id: Mapped[int] = mapped_column(Integer, index=True)
    decision: Mapped[str] = mapped_column(String(32))  # autorejected | exempt
    rule_id: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

Process = FeedItem  # TODO: eliminar en cleanup final
