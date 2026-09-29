"""
Copernicus Data Space Ecosystem client (master plan §7A).

⚠️ BLOCKED:CDSE — written against the documented CDSE STAC + OpenID Connect APIs but
NOT verified against the live service (no credentials in the build environment). The
download strategy spike (§11 Phase 7) is still open; the windowed `/vsicurl/` path is
implemented as the primary route.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderAuthError, ProviderNotConfiguredError, SourceUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)
_token: dict[str, Any] = {"value": None, "expires_at": 0.0}


def _require_config() -> None:
    if not settings.cdse_enabled:
        raise ProviderNotConfiguredError(
            "Copernicus Data Space credentials are not configured. Set CDSE_CLIENT_ID and "
            "CDSE_CLIENT_SECRET in your .env, or upload a Sentinel-2 file instead.",
            {"missing": ["CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET"],
             "alternative": "upload"})


def get_token() -> str:
    _require_config()
    if _token["value"] and _token["expires_at"] > time.time() + 60:
        return _token["value"]
    try:
        r = httpx.post(settings.CDSE_TOKEN_URL, timeout=20, data={
            "grant_type": "client_credentials",
            "client_id": settings.CDSE_CLIENT_ID,
            "client_secret": settings.CDSE_CLIENT_SECRET})
        r.raise_for_status()
        payload = r.json()
    except httpx.HTTPStatusError as exc:
        raise ProviderAuthError(
            "Copernicus rejected the configured credentials.",
            {"status": exc.response.status_code}) from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailableError(f"Could not reach Copernicus: {exc}") from exc

    _token["value"] = payload["access_token"]
    _token["expires_at"] = time.time() + int(payload.get("expires_in", 600))
    return _token["value"]


def search_scenes(aoi_geojson: dict, date_from: str, date_to: str,
                  max_cloud_cover: float = 20.0, limit: int = 20,
                  collection: str = "SENTINEL-2", product_type: str = "L2A") -> list[dict]:
    _require_config()
    coords = aoi_geojson["coordinates"][0]
    bbox = [min(c[0] for c in coords), min(c[1] for c in coords),
            max(c[0] for c in coords), max(c[1] for c in coords)]
    body = {
        "collections": [collection],
        "bbox": bbox,
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "limit": limit,
        "query": {"eo:cloud_cover": {"lte": max_cloud_cover},
                  "productType": {"eq": f"S2MSI{'2A' if product_type == 'L2A' else '1C'}"}},
    }
    try:
        r = httpx.post(f"{settings.CDSE_STAC_URL}/search", json=body, timeout=45,
                       headers={"Authorization": f"Bearer {get_token()}"})
        r.raise_for_status()
        features = r.json().get("features", [])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise ProviderAuthError("Copernicus authorisation failed.", {}) from exc
        if exc.response.status_code == 429:
            raise SourceUnavailableError("Copernicus rate limit reached. Try again shortly.") from exc
        raise SourceUnavailableError(f"Copernicus search failed ({exc.response.status_code}).") from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailableError(f"Copernicus search failed: {exc}") from exc

    return [_normalise(f) for f in features]


def _normalise(f: dict) -> dict:
    props = f.get("properties", {})
    return {
        "external_id": f.get("id"),
        "collection": f.get("collection", "SENTINEL-2"),
        "product_type": props.get("productType", "L2A"),
        "sensed_at": props.get("datetime") or props.get("start_datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "tile_id": props.get("tileId") or props.get("grid:code"),
        "footprint": f.get("geometry"),
        "crs": props.get("proj:epsg") and f"EPSG:{props['proj:epsg']}",
        "available_bands": ["B02", "B03", "B04", "B08", "SCL"],
        "quicklook_url": (f.get("assets", {}).get("QUICKLOOK", {}) or
                          f.get("assets", {}).get("thumbnail", {})).get("href"),
        "size_bytes": props.get("size"),
        "provider": "CDSE",
        "status": "catalog_only",
        # 🔑 Honest null: scene-level cloud % says little about a 5 km AOI. Computing the
        # AOI-level figure needs the SCL band and is a second-stage refinement.
        "aoi_cloud_cover": None,
        "assets": {k: v.get("href") for k, v in (f.get("assets") or {}).items()},
        "source_url": f"https://browser.dataspace.copernicus.eu/?productId={f.get('id')}",
    }


def download_aoi_window(scene, aoi_geojson: dict | None, bands: list[str],
                        out_dir: Path, progress=None) -> Path:
    """
    Read ONLY the AOI window of the requested bands.

    This is what turns a ~1 GB product download into a few MB: rasterio opens the remote
    asset through GDAL's /vsicurl/ driver and issues HTTP range requests for just the
    pixels inside the AOI.
    """
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    _require_config()
    assets = (scene.raster_meta or {}).get("assets") or {}
    token = get_token()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
               CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.jp2",
               GDAL_HTTP_HEADERS=f"Authorization: Bearer {token}")

    stacked, transform, crs, profile = [], None, None, None
    for i, band in enumerate(bands):
        href = assets.get(band) or assets.get(f"{band}_10m")
        if not href:
            raise SourceUnavailableError(
                f"Band {band} is not published for this scene.",
                {"available": sorted(assets)})
        url = f"/vsicurl/{href}" if not href.startswith("/vsi") else href
        with rasterio.Env(**env), rasterio.open(url) as src:
            if aoi_geojson:
                cs = aoi_geojson["coordinates"][0]
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", src.crs, min(c[0] for c in cs), min(c[1] for c in cs),
                    max(c[0] for c in cs), max(c[1] for c in cs))
                window = from_bounds(left, bottom, right, top, src.transform)
                window = window.round_offsets().round_lengths()
            else:
                window = None
            data = src.read(1, window=window)
            if transform is None:
                transform = src.window_transform(window) if window else src.transform
                crs, profile = src.crs, src.profile.copy()
            stacked.append(data)
        if progress:
            progress((i + 1) / len(bands), f"band {band} ({i+1}/{len(bands)})")

    import numpy as np
    arr = np.stack(stacked)
    profile.update(driver="GTiff", count=arr.shape[0], height=arr.shape[1], width=arr.shape[2],
                   transform=transform, crs=crs, tiled=True, compress="deflate")
    out = out_dir / "source.tif"
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr)
        for i, b in enumerate(bands, 1):
            dst.set_band_description(i, b)
    return out
