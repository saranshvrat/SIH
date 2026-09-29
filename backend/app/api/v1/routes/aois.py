from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session, owned_aoi
from app.api.v1.schemas.models_api import AoiCreate, AoiResponse
from app.core.config import settings
from app.core.errors import AOITooLargeError, InvalidAOIError
from app.core.ids import aoi_id as new_aoi_id
from app.db.models import Aoi, Session, in_hours
from app.ml.registry import get_spec
from app.services.geo import measure

router = APIRouter(tags=["aois"])


def _describe(geometry: dict, name: str, model_key: str | None) -> dict:
    area = measure.polygon_area_km2(geometry)
    if area <= 0:
        raise InvalidAOIError("The area of interest has zero area. Draw a valid rectangle or polygon.")

    spec = None
    try:
        spec = get_spec(model_key or settings.ML_DEFAULT_MODEL)
    except Exception:
        pass
    gsd = spec.input_gsd_m if spec else 10.0
    sf = spec.scale_factor if spec else 4
    max_px = spec.max_input_px if spec else settings.MAX_INPUT_PX

    lr_w, lr_h = measure.estimate_pixels(geometry, gsd)
    sr_w, sr_h = lr_w * sf, lr_h * sf
    # 4 bands, 16-bit
    out_mb = round(sr_w * sr_h * 4 * 2 / 1024 / 1024, 1)

    warnings: list[str] = []
    if area > settings.MAX_AOI_KM2:
        raise AOITooLargeError(
            f"This area is {area:.1f} km², above the {settings.MAX_AOI_KM2:g} km² limit. "
            f"Draw a smaller area.",
            {"requested_km2": round(area, 2), "max_km2": settings.MAX_AOI_KM2})
    if max(lr_w, lr_h) > max_px:
        warnings.append(
            f"This area is {lr_w}x{lr_h} pixels at {gsd:g} m. The selected model processes at "
            f"most {max_px} px, so the area will be cropped. Draw a smaller area to avoid this.")
    if out_mb > 200:
        warnings.append(f"The output will be roughly {out_mb:.0f} MB.")

    est_runtime = round(max(4.0, (lr_w * lr_h) / 20000.0), 1)
    return {
        "name": name, "geometry": geometry, "area_km2": round(area, 4),
        "bbox": [round(v, 6) for v in measure.bbox(geometry)],
        "utm_crs": measure.utm_crs_for(geometry),
        "estimated_lr_px": {"width": lr_w, "height": lr_h},
        "estimated_sr_px": {"width": sr_w, "height": sr_h},
        "estimated_output_mb": out_mb, "estimated_runtime_s": est_runtime,
        "warnings": warnings,
    }


@router.post("/aois", response_model=AoiResponse, status_code=201)
def create_aoi(payload: AoiCreate, db: DbSession = Depends(db_session),
               sess: Session = Depends(current_session)) -> AoiResponse:
    info = _describe(payload.geometry.model_dump(), payload.name, payload.model_key)
    aoi = Aoi(id=new_aoi_id(), session_id=sess.id, name=info["name"],
              geometry_geojson=info["geometry"], bbox_minx=info["bbox"][0],
              bbox_miny=info["bbox"][1], bbox_maxx=info["bbox"][2], bbox_maxy=info["bbox"][3],
              area_km2=info["area_km2"], utm_crs=info["utm_crs"],
              expires_at=in_hours(settings.JOB_TTL_HOURS))
    db.add(aoi)
    db.commit()
    return AoiResponse(aoi_id=aoi.id, **info)


@router.post("/aois/preview", response_model=AoiResponse)
def preview_aoi(payload: AoiCreate) -> AoiResponse:
    """Size/cost estimate without persisting. Powers the live readout while drawing."""
    return AoiResponse(aoi_id="preview", **_describe(payload.geometry.model_dump(),
                                                     payload.name, payload.model_key))


@router.get("/aois/{aoi_id}", response_model=AoiResponse)
def get_aoi(aoi=Depends(owned_aoi)) -> AoiResponse:
    info = _describe(aoi.geometry_geojson, aoi.name, None)
    return AoiResponse(aoi_id=aoi.id, **info)
