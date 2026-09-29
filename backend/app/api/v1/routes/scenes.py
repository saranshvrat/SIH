from __future__ import annotations

import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session, owned_scene
from app.api.v1.schemas.models_api import (
    BandMappingUpdate,
    SceneSearchRequest,
    SceneSearchResponse,
    SceneSummary,
)
from app.core.config import settings
from app.core.errors import PayloadTooLargeError, ValidationError
from app.core.ids import scene_id as new_scene_id
from app.db.models import Artifact, Scene, Session, in_hours
from app.services import keys
from app.services.raster.preview import compute_stretch, render_preview
from app.services.raster.validate import validate_raster
from app.services.storage import SCENES, get_storage

router = APIRouter(tags=["scenes"])
CHUNK = 1024 * 1024


def _to_summary(scene: Scene) -> SceneSummary:
    meta = scene.raster_meta or {}
    return SceneSummary(
        scene_id=scene.id,
        external_id=scene.external_id,
        source=scene.source,
        provider=scene.provider,
        collection=scene.collection,
        product_type=scene.product_type,
        sensed_at=scene.sensed_at.isoformat() if scene.sensed_at else None,
        cloud_cover=scene.cloud_cover,
        tile_id=scene.tile_id,
        footprint=scene.footprint_geojson,
        crs=scene.native_crs,
        gsd_m=scene.gsd_m,
        available_bands=list((scene.band_mapping or {}).values()),
        size_bytes=scene.size_bytes,
        status=scene.status,
        original_filename=scene.original_filename,
        band_mapping=scene.band_mapping or {},
        band_mapping_confirmed=scene.band_mapping_confirmed,
        validation=meta.get("validation"),
        source_url=meta.get("source_url"),
        quicklook_url=meta.get("quicklook_url"),
    )


@router.post("/scenes/search", response_model=SceneSearchResponse)
def search_scenes(
    payload: SceneSearchRequest,
    db: DbSession = Depends(db_session),
    sess: Session = Depends(current_session),
) -> SceneSearchResponse:
    from app.services.catalog.planetary_computer import search_scenes as catalog_search

    t0 = time.perf_counter()
    found = catalog_search(
        payload.aoi.model_dump(),
        payload.date_from,
        payload.date_to,
        payload.max_cloud_cover,
        payload.limit,
        payload.collection,
        payload.product_type,
    )
    out: list[SceneSummary] = []
    for f in found:
        scene = Scene(
            id=new_scene_id(),
            session_id=sess.id,
            source="catalog",
            provider=f.get("provider", "Microsoft Planetary Computer"),
            external_id=f["external_id"],
            collection=f["collection"],
            product_type=f["product_type"],
            sensed_at=datetime.fromisoformat(f["sensed_at"].replace("Z", "+00:00"))
            if f.get("sensed_at")
            else None,
            cloud_cover=f.get("cloud_cover"),
            tile_id=f.get("tile_id"),
            footprint_geojson=f.get("footprint"),
            native_crs=f.get("crs"),
            gsd_m=10.0,
            band_mapping={str(i + 1): b for i, b in enumerate(f["available_bands"])},
            band_mapping_confirmed=True,
            status="catalog_only",
            raster_meta={
                "assets": f.get("assets", {}),
                "source_url": f.get("source_url"),
                "quicklook_url": f.get("quicklook_url"),
                "processing_baseline": f.get("processing_baseline"),
                "band_count": len(f.get("available_bands", [])),
            },
            size_bytes=f.get("size_bytes"),
            expires_at=in_hours(settings.SCENE_CACHE_TTL_HOURS),
        )
        db.add(scene)
        db.flush()
        s = _to_summary(scene)
        s.aoi_cloud_cover = f.get("aoi_cloud_cover")
        out.append(s)
    db.commit()
    return SceneSearchResponse(
        scenes=out,
        total=len(out),
        provider_query_ms=int((time.perf_counter() - t0) * 1000),
        notes=[
            "Cloud cover shown is for the whole Sentinel-2 tile (about 110 km across), "
            "not for your area of interest."
        ],
    )


@router.post("/scenes/upload", response_model=SceneSummary, status_code=201)
async def upload_scene(
    file: UploadFile = File(...),
    force: bool = Form(False),
    db: DbSession = Depends(db_session),
    sess: Session = Depends(current_session),
) -> SceneSummary:
    """Streaming upload — the file is never read into memory in one piece."""
    storage = get_storage()
    tmpdir = Path(tempfile.mkdtemp(prefix="pf_upload_"))
    local = tmpdir / (Path(file.filename or "upload.tif").name)
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(local, "wb") as out:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > limit:
                    raise PayloadTooLargeError(
                        f"This file is larger than the {settings.MAX_UPLOAD_MB} MB upload limit. "
                        f"Crop the area of interest in QGIS and upload the subset.",
                        {"limit_mb": settings.MAX_UPLOAD_MB},
                    )
                out.write(chunk)

        report = validate_raster(
            local,
            file.filename or "upload.tif",
            required_bands=4,
            expected_gsd_m=10.0,
            force=force,
        )
        checksum = storage.checksum(local)

        existing = (
            db.query(Scene)
            .filter(Scene.session_id == sess.id, Scene.checksum_sha256 == checksum)
            .first()
        )
        if existing:
            return _to_summary(existing)

        scene = Scene(
            id=new_scene_id(),
            session_id=sess.id,
            source="upload",
            provider="user",
            product_type="L2A",
            native_crs=report["crs"],
            gsd_m=report["gsd_m"][0],
            footprint_geojson=_bbox_polygon(report["bounds_wgs84"]),
            bbox_minx=report["bounds_wgs84"][0],
            bbox_miny=report["bounds_wgs84"][1],
            bbox_maxx=report["bounds_wgs84"][2],
            bbox_maxy=report["bounds_wgs84"][3],
            band_mapping=report["detected_band_mapping"],
            band_mapping_confirmed=report["band_mapping_confident"],
            raster_meta={"validation": report},
            original_filename=file.filename,
            checksum_sha256=checksum,
            size_bytes=written,
            status="ready"
            if report["band_mapping_confident"]
            else "needs_confirmation",
            expires_at=in_hours(settings.JOB_TTL_HOURS),
        )
        key = keys.scene_source(scene.id, Path(file.filename or "upload.tif").name)
        storage.put_file(SCENES, key, local)
        scene.storage_key = key
        db.add(scene)
        db.flush()

        db.add(
            Artifact(
                session_id=sess.id,
                owner_type="scene",
                owner_id=scene.id,
                kind="source",
                format="GTiff",
                bucket=SCENES,
                storage_key=key,
                bytes=written,
                checksum_sha256=checksum,
                expires_at=scene.expires_at,
            )
        )
        try:
            ql = tmpdir / "quicklook.webp"
            names = list(report["detected_band_mapping"].values())
            render_preview(
                local,
                ql,
                names,
                "rgb_true",
                compute_stretch(local, [1, 2, 3]),
                max_size=768,
            )
            qkey = keys.scene_quicklook(scene.id)
            storage.put_file(SCENES, qkey, ql)
            scene.raster_meta = {
                **scene.raster_meta,
                "quicklook_url": f"{settings.API_V1_PREFIX}/scenes/{scene.id}/quicklook",
            }
        except Exception:
            pass
        db.commit()
        return _to_summary(scene)
    finally:
        await file.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _bbox_polygon(b: list[float]) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]
        ],
    }


@router.get("/scenes", response_model=list[SceneSummary])
def list_scenes(
    db: DbSession = Depends(db_session), sess: Session = Depends(current_session)
) -> list[SceneSummary]:
    rows = (
        db.query(Scene)
        .filter(Scene.session_id == sess.id)
        .order_by(Scene.created_at.desc())
        .limit(50)
        .all()
    )
    return [_to_summary(s) for s in rows]


@router.get("/scenes/{scene_id}", response_model=SceneSummary)
def get_scene(scene=Depends(owned_scene)) -> SceneSummary:
    return _to_summary(scene)


@router.patch("/scenes/{scene_id}/bands", response_model=SceneSummary)
def confirm_bands(
    payload: BandMappingUpdate,
    scene=Depends(owned_scene),
    db: DbSession = Depends(db_session),
) -> SceneSummary:
    allowed = {"B02", "B03", "B04", "B08", "B8A", "SCL"}
    bad = set(payload.band_mapping.values()) - allowed
    if bad:
        raise ValidationError(
            f"Unknown band name(s): {', '.join(sorted(bad))}.",
            {"allowed": sorted(allowed)},
        )
    band_count = (scene.raster_meta or {}).get("validation", {}).get("band_count") or (
        scene.raster_meta or {}
    ).get("band_count")
    if band_count:
        bad_idx = [k for k in payload.band_mapping if not (1 <= int(k) <= band_count)]
        if bad_idx:
            raise ValidationError(
                f"Band index/indexes {', '.join(sorted(bad_idx))} out of range: this "
                f"file only has {band_count} band(s) (valid range 1-{band_count}).",
                {"band_count": band_count, "invalid_keys": bad_idx},
            )
    scene.band_mapping = payload.band_mapping
    scene.band_mapping_confirmed = True
    scene.status = "ready"
    db.add(scene)
    db.commit()
    return _to_summary(scene)


@router.delete("/scenes/{scene_id}", status_code=204, response_model=None)
def delete_scene(
    scene=Depends(owned_scene), db: DbSession = Depends(db_session)
) -> None:
    storage = get_storage()
    storage.delete_prefix(SCENES, f"scenes/{scene.id}")
    db.delete(scene)
    db.commit()
