"""
PixelForge schema — EPHEMERAL session model (ADR-001).

There is no `users` table. Everything hangs off an anonymous `sessions` row and carries
an `expires_at`. A sweeper deletes expired rows and their storage objects.

Geometry is stored as GeoJSON + bbox floats rather than PostGIS geometry columns
(ADR-002), which keeps the ORM dialect-portable while losing no feature.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.ids import (analysis_id, artifact_id, chat_id, job_id, report_id, run_id,
                          scene_id, session_id)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def in_hours(h: int) -> datetime:
    return utcnow() + timedelta(hours=h)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class Session(Base):
    """Anonymous browser session. The ONLY notion of identity in PixelForge."""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=session_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=scene_id)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)

    source: Mapped[str] = mapped_column(String(16))          # catalog | upload
    provider: Mapped[str] = mapped_column(String(32), default="user")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collection: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sensed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cloud_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    tile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ADR-002: GeoJSON + bbox instead of PostGIS geometry
    footprint_geojson: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bbox_minx: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_miny: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_maxx: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_maxy: Mapped[float | None] = mapped_column(Float, nullable=True)

    native_crs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gsd_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    band_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    band_mapping_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    raster_meta: Mapped[dict] = mapped_column(JSON, default=dict)

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(String(24), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        UniqueConstraint("session_id", "checksum_sha256", name="uq_scene_session_checksum"),
        Index("ix_scene_session_created", "session_id", "created_at"),
    )


class Aoi(Base):
    __tablename__ = "aois"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="Area of interest")
    geometry_geojson: Mapped[dict] = mapped_column(JSON)
    bbox_minx: Mapped[float] = mapped_column(Float)
    bbox_miny: Mapped[float] = mapped_column(Float)
    bbox_maxx: Mapped[float] = mapped_column(Float)
    bbox_maxy: Mapped[float] = mapped_column(Float)
    area_km2: Mapped[float] = mapped_column(Float)
    utm_crs: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=job_id)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str] = mapped_column(String(64), ForeignKey("scenes.id", ondelete="CASCADE"))
    aoi_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("aois.id", ondelete="SET NULL"), nullable=True)

    mode: Mapped[str] = mapped_column(String(16), default="inference")   # inference | validation
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    current_stage: Mapped[str] = mapped_column(String(32), default="QUEUED")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stages: Mapped[list] = mapped_column(JSON, default=list)

    requested_models: Mapped[list] = mapped_column(JSON, default=list)
    auto_select_model: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_models: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    terrain_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terrain_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_fraction_aoi: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    partial_success: Mapped[bool] = mapped_column(Boolean, default=False)

    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    runs: Mapped[list["ModelRun"]] = relationship(back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_job_session_idem"),
        Index("ix_job_session_created", "session_id", "created_at"),
    )


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=run_id)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    model_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")

    # Provenance snapshot at run time — answers "which exact model produced this?"
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ml_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weights_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    input_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_gsd_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_bands: Mapped[list] = mapped_column(JSON, default=list)
    output_crs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_bounds_wgs84: Mapped[list | None] = mapped_column(JSON, nullable=True)
    output_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_dtype: Mapped[str | None] = mapped_column(String(16), nullable=True)

    geospatial_integrity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tiles_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="runs")


class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("model_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="overall")
    band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tile_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 🚨 Every metric carries its reference type. A number without provenance is a claim.
    reference_type: Mapped[str] = mapped_column(String(24), default="none")
    reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    implementation: Mapped[str] = mapped_column(String(64), default="pixelforge.metrics@0.1.0")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "name", "scope", "band", "tile_index", name="uq_metric_identity"),
    )


class UncertaintyMap(Base):
    __tablename__ = "uncertainty_maps"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=artifact_id)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("model_runs.id", ondelete="CASCADE"), nullable=True)
    method: Mapped[str] = mapped_column(String(48))
    # 🚨 Honesty as a schema property, not a UI afterthought. Defaults to false.
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    statistic: Mapped[str] = mapped_column(String(64), default="per_pixel_std_of_reflectance")
    unit: Mapped[str] = mapped_column(String(32), default="reflectance")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_definition: Mapped[str] = mapped_column(Text, default="")
    source_run_ids: Mapped[list] = mapped_column(JSON, default=list)
    high_uncertainty_regions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=analysis_id)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("model_runs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    lr_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    comparison_with_lr: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_on: Mapped[str] = mapped_column(String(16), default="both")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    """One table for every file. Polymorphic owner."""
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=artifact_id)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    owner_type: Mapped[str] = mapped_column(String(24))
    owner_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(48))
    format: Mapped[str] = mapped_column(String(16))
    bucket: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bounds_wgs84: Mapped[list | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("ix_artifact_owner", "owner_type", "owner_id"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=report_id)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    format: Mapped[str] = mapped_column(String(16), default="pdf")
    artifact_id_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=chat_id)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_session_id: Mapped[str] = mapped_column(String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    # Stored because they power the transparency panel AND are the audit trail
    # against any claim that the analyst invented a number.
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    tool_results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
