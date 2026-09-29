"""
Microsoft Planetary Computer Sentinel-2 L2A client.

Replaces the Copernicus Data Space Ecosystem client (`cdse.py`, kept in the tree but no
longer imported anywhere): Planetary Computer's STAC catalogue is public and needs no
account, no client credentials, and no OpenID token dance. `search_scenes` and
`download_aoi_window` below are drop-in replacements for CDSE's — same call signatures,
same normalised dict / GeoTIFF outputs — so `routes/scenes.py` and `domains/pipeline.py`
don't need to know which provider is behind them.

Same STAC search + windowed-read approach as the `wsx4_spatrad_eval_lat_lon` evaluation
notebook, which fetches individual point crops from this exact catalogue.

Asset hrefs are stored UNSIGNED (see `search_scenes`): Planetary Computer's signed URLs
are short-lived SAS tokens (about an hour), far shorter than this app's
SCENE_CACHE_TTL_HOURS (72h by default). `download_aoi_window` calls
`planetary_computer.sign()` right before opening each band, so a scene searched hours
earlier still downloads correctly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import planetary_computer
import pystac_client

from app.core.errors import SourceUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
# What the rest of the app expects to find as assets on a scene (see WANTED_BANDS in
# domains/pipeline.py). SCL (scene classification layer) is carried along for an optional
# cloud mask; it isn't required for inference.
KNOWN_BANDS = ["B02", "B03", "B04", "B08", "SCL"]

_catalog: pystac_client.Client | None = None


def _client() -> pystac_client.Client:
    global _catalog
    if _catalog is None:
        # No `modifier=sign_inplace` here on purpose — see the module docstring: hrefs
        # are signed lazily, at download time, never baked into stored/cached metadata.
        _catalog = pystac_client.Client.open(STAC_URL)
    return _catalog


def search_scenes(aoi_geojson: dict, date_from: str, date_to: str,
                  max_cloud_cover: float = 20.0, limit: int = 20,
                  collection: str = "SENTINEL-2", product_type: str = "L2A") -> list[dict]:
    """
    `collection`/`product_type` are accepted only to keep the same call signature CDSE
    used (see SceneSearchRequest) — Planetary Computer's public catalogue serves
    Sentinel-2 as a single L2A collection, so both are ignored.
    """
    coords = aoi_geojson["coordinates"][0]
    bbox = [min(c[0] for c in coords), min(c[1] for c in coords),
            max(c[0] for c in coords), max(c[1] for c in coords)]
    try:
        search = _client().search(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            query={"eo:cloud_cover": {"lt": max_cloud_cover}},
            limit=limit,
        )
        items = list(search.items())
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailableError(
            f"Microsoft Planetary Computer search failed: {exc}") from exc

    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 100))
    return [_normalise(it) for it in items[:limit]]


def _normalise(item: Any) -> dict:
    props = item.properties
    assets = item.assets
    preview = assets.get("rendered_preview")
    epsg = props.get("proj:epsg")
    return {
        "external_id": item.id,
        "collection": "SENTINEL-2",
        "product_type": "L2A",
        "sensed_at": props.get("datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "tile_id": props.get("s2:mgrs_tile"),
        "footprint": item.geometry,
        "crs": f"EPSG:{epsg}" if epsg else None,
        "available_bands": [b for b in KNOWN_BANDS if b in assets],
        # `rendered_preview` is served by Planetary Computer's own public rendering API
        # and needs no signing — unlike the raw band assets below.
        "quicklook_url": preview.href if preview else None,
        "size_bytes": None,  # not published per-asset by this collection
        "provider": "Microsoft Planetary Computer",
        "status": "catalog_only",
        # 🔑 Honest null: scene-level cloud % says little about a small AOI (see cdse.py
        # for the same convention CDSE used).
        "aoi_cloud_cover": None,
        # Stored UNSIGNED — see module docstring.
        "assets": {k: a.href for k, a in assets.items() if k in KNOWN_BANDS},
        "processing_baseline": props.get("s2:processing_baseline"),
        "source_url": f"https://planetarycomputer.microsoft.com/explore?"
                      f"collection={COLLECTION}&item={item.id}",
    }


def _harmonize_to_old(data, baseline):
    """Scenes processed under baseline >= 04.00 (post 2022-01-25) carry a +1000 DN
    offset; subtract it back out for radiometric consistency with older scenes. Same
    correction the wsx4_spatrad evaluation notebook applies before inference."""
    if not baseline:
        return data
    try:
        major, minor = (int(x) for x in str(baseline).split(".")[:2])
    except ValueError:
        return data
    if (major, minor) >= (4, 0):
        import numpy as np
        shifted = data.astype("int32") - 1000
        np.clip(shifted, 0, None, out=shifted)
        return shifted.astype(data.dtype)
    return data


def download_aoi_window(scene, aoi_geojson: dict | None, bands: list[str],
                        out_dir: Path, progress=None) -> Path:
    """
    Read ONLY the AOI window of the requested bands — same strategy as CDSE's version:
    rasterio opens each remote, signed COG through GDAL's /vsicurl/ driver and issues
    HTTP range requests for just the pixels inside the AOI, so a multi-GB scene never has
    to be downloaded whole.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    meta = scene.raster_meta or {}
    assets = meta.get("assets") or {}
    baseline = meta.get("processing_baseline")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Same GDAL network-hardening the eval notebook uses — a stalled band read otherwise
    # hangs forever with no timeout.
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
               GDAL_HTTP_TIMEOUT="30", GDAL_HTTP_CONNECTTIMEOUT="15",
               GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2")

    stacked, transform, crs, profile = [], None, None, None
    for i, band in enumerate(bands):
        href = assets.get(band)
        if not href:
            raise SourceUnavailableError(
                f"Band {band} is not published for this scene.",
                {"available": sorted(assets)})
        try:
            signed = planetary_computer.sign(href)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailableError(
                f"Could not sign the Planetary Computer asset URL for {band}: {exc}"
            ) from exc
        url = f"/vsicurl/{signed}"
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
            data = _harmonize_to_old(data, baseline)
            if transform is None:
                transform = src.window_transform(window) if window else src.transform
                crs, profile = src.crs, src.profile.copy()
            stacked.append(data)
        if progress:
            progress((i + 1) / len(bands), f"band {band} ({i + 1}/{len(bands)})")

    arr = np.stack(stacked)
    profile.update(driver="GTiff", count=arr.shape[0], height=arr.shape[1], width=arr.shape[2],
                   transform=transform, crs=crs, tiled=True, compress="deflate")
    out = out_dir / "source.tif"
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr)
        for i, b in enumerate(bands, 1):
            dst.set_band_description(i, b)
    return out
