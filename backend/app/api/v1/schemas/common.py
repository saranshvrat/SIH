from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class GeoJSONPolygon(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def _valid_ring(cls, v):
        if not v or len(v[0]) < 4:
            raise ValueError("a polygon ring needs at least 4 positions")
        for lon, lat in ((p[0], p[1]) for p in v[0]):
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"coordinate out of range: {lon},{lat}")
        return v


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    database: bool
    storage: dict[str, Any]
    redis: bool
    gpu_workers_online: int
    models_available: list[str]
    features: dict[str, bool]
    contract_version: str
