"""COG conversion + provenance tagging (master plan §7I, §9.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy


def to_cog(src_path: Path, dst_path: Path, tags: dict[str, Any] | None = None,
           band_names: list[str] | None = None, overview_levels=(2, 4, 8, 16)) -> Path:
    """
    Write a spec-compliant Cloud-Optimized GeoTIFF with internal overviews.

    "Analysis-ready output" is literal problem-statement language: the file must carry
    its own provenance so it travels with the data, not just in our database.
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(src_path) as src:
        profile = src.profile.copy()
        profile.update(driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate", predictor=2, BIGTIFF="IF_SAFER")
        tmp = dst_path.with_suffix(".tmp.tif")
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(src.read())
            names = band_names or [d for d in (src.descriptions or []) if d]
            for i, n in enumerate(names[: src.count], 1):
                dst.set_band_description(i, n)
            merged = dict(src.tags())
            merged.update({k: str(v) for k, v in (tags or {}).items()})
            dst.update_tags(**merged)
            levels = [lv for lv in overview_levels
                      if min(src.height, src.width) // lv >= 32]
            if levels:
                dst.build_overviews(levels, Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")

    rio_copy(tmp, dst_path, driver="GTiff", copy_src_overviews=True, tiled=True,
             blockxsize=512, blockysize=512, compress="deflate", predictor=2,
             BIGTIFF="IF_SAFER")
    tmp.unlink(missing_ok=True)
    return dst_path


def is_valid_cog(path: Path) -> dict[str, Any]:
    with rasterio.open(path) as src:
        return {"tiled": src.is_tiled,
                "blocksize": list(src.block_shapes[0]) if src.block_shapes else None,
                "overviews": list(src.overviews(1)),
                "compression": str(src.compression) if src.compression else None,
                "valid": bool(src.is_tiled)}


def raster_summary(path: Path) -> dict[str, Any]:
    from rasterio.warp import transform_bounds
    with rasterio.open(path) as src:
        return {
            "width": src.width, "height": src.height, "band_count": src.count,
            "dtype": src.dtypes[0], "crs": str(src.crs),
            "gsd_m": round(float(src.res[0]), 4),
            "bounds_native": [round(v, 3) for v in src.bounds],
            "bounds_wgs84": [round(v, 6) for v in transform_bounds(src.crs, "EPSG:4326", *src.bounds)],
            "transform": list(src.transform)[:6],
            "band_descriptions": [d or None for d in (src.descriptions or [])],
            "tags": src.tags(),
        }
