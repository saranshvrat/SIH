from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.common import GeoJSONPolygon


class ModelInfo(BaseModel):
    key: str
    display_name: str
    description: str
    status: Literal["available", "unavailable"]
    unavailable_reason: str | None = None
    is_baseline: bool
    scale_factor: int
    input_gsd_m: float
    output_gsd_m: float
    input_bands: list[str]
    output_bands: list[str]
    requires_gpu: bool
    supports_uncertainty: bool
    supports_sampling: bool
    max_input_px: int
    tile_px: int
    tile_overlap_px: int
    best_for: list[str]
    citation_url: str = ""
    verified_against_repo: bool
    health: dict[str, Any] = Field(default_factory=dict)


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
    default_model: str
    contract_version: str


# --- AOI -------------------------------------------------------------------
class AoiCreate(BaseModel):
    # `model_key` collides with pydantic v2's reserved "model_" prefix. The field name is
    # part of the public API, so keep it and switch the protection off for this class.
    model_config = ConfigDict(protected_namespaces=())

    name: str = "Area of interest"
    geometry: GeoJSONPolygon
    model_key: str | None = None


class AoiResponse(BaseModel):
    aoi_id: str
    name: str
    geometry: dict
    area_km2: float
    bbox: list[float]
    utm_crs: str
    estimated_lr_px: dict[str, int]
    estimated_sr_px: dict[str, int]
    estimated_output_mb: float
    estimated_runtime_s: float | None = None
    warnings: list[str] = Field(default_factory=list)


# --- scenes ----------------------------------------------------------------
class SceneSearchRequest(BaseModel):
    aoi: GeoJSONPolygon
    date_from: str
    date_to: str
    max_cloud_cover: float = 20.0
    collection: str = "SENTINEL-2"
    product_type: str = "L2A"
    limit: int = Field(20, ge=1, le=50)


class SceneSummary(BaseModel):
    scene_id: str | None = None
    external_id: str | None = None
    source: str
    provider: str
    collection: str | None = None
    product_type: str | None = None
    sensed_at: str | None = None
    cloud_cover: float | None = None
    aoi_cloud_cover: float | None = None
    tile_id: str | None = None
    footprint: dict | None = None
    crs: str | None = None
    gsd_m: float | None = None
    available_bands: list[str] = Field(default_factory=list)
    quicklook_url: str | None = None
    size_bytes: int | None = None
    status: str = "catalog_only"
    source_url: str | None = None
    original_filename: str | None = None
    band_mapping: dict[str, str] = Field(default_factory=dict)
    band_mapping_confirmed: bool = False
    validation: dict[str, Any] | None = None


class SceneSearchResponse(BaseModel):
    scenes: list[SceneSummary]
    total: int
    provider_query_ms: int
    notes: list[str] = Field(default_factory=list)


class BandMappingUpdate(BaseModel):
    band_mapping: dict[str, str]


# --- jobs ------------------------------------------------------------------
class JobOptions(BaseModel):
    band_combination: list[str] = Field(default_factory=lambda: ["B02", "B03", "B04", "B08"])
    cloud_mask: bool = True
    compute_metrics: bool = True
    compute_uncertainty: bool = True
    post_process: bool = False
    analyses: list[str] = Field(default_factory=list)
    reference: dict[str, Any] | None = None


class JobCreate(BaseModel):
    scene_id: str
    aoi_id: str | None = None
    mode: Literal["inference", "validation"] = "inference"
    models: list[str] = Field(default_factory=list)
    auto_select_model: bool = False
    options: JobOptions = Field(default_factory=JobOptions)
    idempotency_key: str | None = None


class JobCreateResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    estimated_duration_s: float | None = None
    poll_url: str
    already_existed: bool = False


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    mode: str
    progress: float
    current_stage: str
    stage_detail: str | None = None
    stages: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    terrain: dict[str, Any] | None = None
    cloud_fraction_aoi: float | None = None
    partial_success: bool = False
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str | None = None
    duration_s: float | None = None
    expires_at: str
