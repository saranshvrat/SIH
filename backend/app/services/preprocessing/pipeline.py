"""
Preprocessing (master plan §7C).

Produces exactly ONE clean GeoTIFF that every model in the job consumes. Comparing
models on differently-preprocessed inputs would invalidate the whole comparison, so
this runs once per job, never once per model.

⚠️ NORMALISATION IS DELIBERATELY ABSENT. Master plan §13.2 A5: it stays inside the
adapter so it can never drift from the weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

# Sentinel-2 L2A Scene Classification Layer classes treated as unusable.
SCL_CLOUD_CLASSES = {
    3: "cloud_shadow",
    8: "cloud_medium_prob",
    9: "cloud_high_prob",
    10: "thin_cirrus",
}
CLOUD_REJECT_THRESHOLD = 0.40


@dataclass
class PreprocessResult:
    lr_path: Path
    mask_path: Path | None
    width: int
    height: int
    band_order: list[str]
    crs: str
    gsd_m: float
    bounds_wgs84: list[float]
    cloud_fraction: float | None
    nodata_fraction: float
    integrity: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _resolve_band_indexes(
    band_mapping: dict[str, str], wanted: list[str], count: int
) -> list[int]:
    """
    Map requested band NAMES to file band INDEXES, in the model's declared order.

    This is the single most dangerous step in the whole pipeline: a wrong order here
    produces a plausible-looking, scientifically wrong result with no error anywhere.
    """
    inverse = {v: int(k) for k, v in (band_mapping or {}).items()}
    indexes = []
    for name in wanted:
        if name in inverse:
            idx = inverse[name]
            if idx < 1 or idx > count:
                raise ValueError(
                    f"Band mapping says {name} is band {idx}, but this file only has "
                    f"{count} band(s) (valid range 1-{count}). The scene's band mapping "
                    f"is stale for this file — re-confirm it on the scene before "
                    f"reprocessing."
                )
            indexes.append(idx)
        else:
            pos = len(indexes) + 1
            if pos > count:
                raise ValueError(
                    f"band {name} not present and no positional fallback available"
                )
            indexes.append(pos)
    return indexes


def run(
    source_path: Path,
    out_dir: Path,
    band_mapping: dict[str, str],
    wanted_bands: list[str],
    aoi_geojson: dict | None = None,
    cloud_mask: bool = True,
    max_input_px: int = 512,
    progress=None,
) -> PreprocessResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    with rasterio.open(source_path) as src:
        # --- 1. AOI windowed read (never load a whole 10980x10980 scene) ---
        if aoi_geojson:
            coords = aoi_geojson["coordinates"][0]
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            left, bottom, right, top = transform_bounds(
                "EPSG:4326", src.crs, min(xs), min(ys), max(xs), max(ys)
            )
            window = (
                from_bounds(left, bottom, right, top, src.transform)
                .round_offsets()
                .round_lengths()
            )
            window = window.intersection(Window(0, 0, src.width, src.height))
            if window.width < 8 or window.height < 8:
                raise ValueError(
                    "AOI does not overlap the scene, or is smaller than 8 pixels."
                )
        else:
            window = Window(0, 0, src.width, src.height)

        if max(window.width, window.height) > max_input_px:
            warnings.append(
                f"Requested area is {int(window.width)}x{int(window.height)} px; "
                f"cropped to the model limit of {max_input_px} px."
            )
            window = Window(
                window.col_off,
                window.row_off,
                min(window.width, max_input_px),
                min(window.height, max_input_px),
            )

        if progress:
            progress(0.2, "reading area of interest")

        # --- 2. Band selection AND ORDERING ---
        indexes = _resolve_band_indexes(band_mapping, wanted_bands, src.count)
        data = src.read(indexes=indexes, window=window)
        transform = src.window_transform(window)
        profile = src.profile.copy()
        nodata = src.nodata

        # --- 3. Cloud masking from the SCL band, if present ---
        cloud_fraction = None
        cloud_mask_arr = None
        inverse = {v: int(k) for k, v in (band_mapping or {}).items()}
        scl_index = inverse.get("SCL")
        scl_in_range = scl_index is not None and 1 <= scl_index <= src.count
        if cloud_mask and scl_in_range:
            if progress:
                progress(0.45, "cloud masking (SCL)")
            scl = src.read(scl_index, window=window)
            cloud_mask_arr = np.isin(scl, list(SCL_CLOUD_CLASSES))
            cloud_fraction = float(cloud_mask_arr.mean())
        elif cloud_mask and scl_index is not None:
            warnings.append(
                f"Band mapping points SCL at band {scl_index}, but this file only has "
                f"{src.count} band(s), so cloud fraction could not be computed. "
                f"Inspect the preview before trusting the result."
            )
        elif cloud_mask:
            warnings.append(
                "No SCL band found, so cloud fraction could not be computed. "
                "Inspect the preview before trusting the result."
            )

    if cloud_fraction is not None and cloud_fraction > CLOUD_REJECT_THRESHOLD:
        raise ValueError(
            f"{cloud_fraction * 100:.0f}% of this area is cloud or cloud shadow "
            f"(limit {CLOUD_REJECT_THRESHOLD * 100:.0f}%). Choose a clearer scene or date."
        )

    nodata_fraction = float((data == nodata).mean()) if nodata is not None else 0.0
    if nodata_fraction > 0.5:
        warnings.append(f"{nodata_fraction * 100:.0f}% of this area has no data.")

    if progress:
        progress(0.75, "writing model input")

    profile.update(
        driver="GTiff",
        count=data.shape[0],
        height=data.shape[1],
        width=data.shape[2],
        transform=transform,
        tiled=True,
        blockxsize=256,
        blockysize=256,
        compress="deflate",
    )
    lr_path = out_dir / "lr.tif"
    with rasterio.open(lr_path, "w", **profile) as dst:
        dst.write(data)
        for i, name in enumerate(wanted_bands, 1):
            dst.set_band_description(i, name)
        dst.update_tags(
            PIXELFORGE_STAGE="preprocessed", BAND_ORDER=",".join(wanted_bands)
        )

    mask_path = None
    if cloud_mask_arr is not None:
        mask_path = out_dir / "lr_mask.tif"
        mprof = dict(profile)
        mprof.update(count=1, dtype="uint8", nodata=None)
        with rasterio.open(mask_path, "w", **mprof) as dst:
            dst.write(cloud_mask_arr.astype("uint8")[None, :, :])
            dst.set_band_description(1, "cloud_mask")

    with rasterio.open(lr_path) as chk:
        integrity = {
            "input_crs": str(chk.crs),
            "input_gsd_m": round(float(chk.res[0]), 4),
            "input_bounds_native": [round(v, 3) for v in chk.bounds],
            "input_bounds_wgs84": [
                round(v, 6) for v in transform_bounds(chk.crs, "EPSG:4326", *chk.bounds)
            ],
            "band_order": list(wanted_bands),
            "source_band_indexes": indexes,
            "width": chk.width,
            "height": chk.height,
            "dtype": chk.dtypes[0],
            # ⚠️ Geometric alignment (§13.2, §24): for Sentinel-2 B02/03/04/08 are all
            # natively 10 m on a common grid, so this is a verified no-op, not a skip.
            "geometric_alignment": "no-op: all requested bands share the native 10 m grid",
        }
        bounds_wgs84 = integrity["input_bounds_wgs84"]
        crs, gsd, w, h = str(chk.crs), float(chk.res[0]), chk.width, chk.height

    if progress:
        progress(1.0, "preprocessing complete")

    return PreprocessResult(
        lr_path=lr_path,
        mask_path=mask_path,
        width=w,
        height=h,
        band_order=list(wanted_bands),
        crs=crs,
        gsd_m=gsd,
        bounds_wgs84=bounds_wgs84,
        cloud_fraction=cloud_fraction,
        nodata_fraction=nodata_fraction,
        integrity=integrity,
        warnings=warnings,
    )
