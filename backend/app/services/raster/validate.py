"""Upload validation (master plan §7B). Validates by OPENING with rasterio, never by extension."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

from app.core.errors import (CorruptRasterError, InsufficientBandsError,
                             InvalidRasterGSDError, MissingCRSError, NotGeoreferencedError,
                             UnsupportedFormatError)

ALLOWED_SUFFIXES = {".tif", ".tiff", ".jp2"}
ALLOWED_DRIVERS = {"GTiff", "JP2OpenJPEG", "COG", "JPEG2000"}
S2_BAND_ORDER = ["B02", "B03", "B04", "B08"]
S2_ALIASES = {
    "b02": "B02", "b2": "B02", "blue": "B02",
    "b03": "B03", "b3": "B03", "green": "B03",
    "b04": "B04", "b4": "B04", "red": "B04",
    "b08": "B08", "b8": "B08", "nir": "B08", "b8a": "B8A",
    "scl": "SCL",
}


def check_suffix(filename: str) -> None:
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise UnsupportedFormatError(
            f"Unsupported file type {Path(filename).suffix!r}. "
            f"PixelForge accepts georeferenced GeoTIFF (.tif/.tiff) or JPEG2000 (.jp2). "
            f"PNG and JPEG carry no coordinate reference system and cannot be processed.",
            {"allowed": sorted(ALLOWED_SUFFIXES)})


def identify_bands(src, filename: str) -> tuple[dict[str, str], bool, list[str]]:
    """
    Four-level band identification ladder (master plan §7B).
    Returns (mapping, confident, warnings).
    """
    warnings: list[str] = []

    # Level 1: GDAL band descriptions.
    descs = [d or "" for d in (src.descriptions or [])]
    mapped = {}
    for i, d in enumerate(descs, 1):
        key = S2_ALIASES.get(d.strip().lower())
        if key:
            mapped[str(i)] = key
    if len(mapped) >= min(4, src.count):
        return mapped, True, warnings

    # Level 2: Sentinel-2 filename pattern (e.g. T45QVE_20260214T051231_B02_10m.jp2).
    stem = Path(filename).stem.lower()
    for alias, band in S2_ALIASES.items():
        if f"_{alias}_" in f"_{stem}_" and src.count == 1:
            return {"1": band}, True, warnings

    # Level 3: sidecar metadata tags.
    tags = src.tags()
    if "BAND_MAPPING" in tags:
        try:
            import json
            return json.loads(tags["BAND_MAPPING"]), True, warnings
        except Exception:
            pass

    # Level 4: positional assumption — NOT confident.
    mapping = {str(i + 1): S2_BAND_ORDER[i] for i in range(min(src.count, 4))}
    warnings.append(
        "Band names were not found in the file metadata. The mapping below was inferred "
        "from band order and must be confirmed before processing.")
    return mapping, False, warnings


def validate_raster(path: Path, filename: str, required_bands: int = 4,
                    expected_gsd_m: float = 10.0, gsd_tolerance: float = 0.25,
                    force: bool = False) -> dict[str, Any]:
    """Open, inspect and validate. Raises a typed error, or returns a metadata report."""
    check_suffix(filename)
    try:
        src = rasterio.open(path)
    except Exception as exc:  # noqa: BLE001
        raise CorruptRasterError(
            "This file could not be opened as a raster. It may be corrupt, truncated, "
            "or not actually an image.", {"reason": type(exc).__name__}) from exc

    with src:
        if src.driver not in ALLOWED_DRIVERS:
            raise UnsupportedFormatError(f"Unsupported raster driver {src.driver!r}.",
                                         {"driver": src.driver})
        if src.transform is None or src.transform.is_identity:
            raise NotGeoreferencedError(
                "This image has no affine transform, so it cannot be placed on the Earth. "
                "PixelForge needs georeferenced satellite imagery.")
        if src.crs is None:
            raise MissingCRSError(
                "This image has no coordinate reference system (CRS). Export it from your "
                "GIS with a CRS set (Sentinel-2 products are normally in a UTM zone).")
        if src.count < required_bands:
            raise InsufficientBandsError(
                f"The selected model needs {required_bands} bands "
                f"({', '.join(S2_BAND_ORDER)}) but this file has {src.count}.",
                {"required": required_bands, "actual": src.count})

        gsd_x, gsd_y = float(src.res[0]), float(src.res[1])
        warnings: list[str] = []
        if expected_gsd_m and not force:
            lo, hi = expected_gsd_m * (1 - gsd_tolerance), expected_gsd_m * (1 + gsd_tolerance)
            if not (lo <= gsd_x <= hi):
                raise InvalidRasterGSDError(
                    f"Input ground sample distance is {gsd_x:.1f} m, but the selected model "
                    f"expects approximately {expected_gsd_m:.0f} m Sentinel-2 input. "
                    f"Re-submit with force=true to override.",
                    {"detected_gsd_m": [gsd_x, gsd_y], "expected_gsd_m": expected_gsd_m})
        elif force and abs(gsd_x - expected_gsd_m) > 0.1:
            warnings.append(
                f"GSD {gsd_x:.1f} m differs from the model's expected {expected_gsd_m:.0f} m. "
                f"Results may be unreliable and metrics are not comparable to benchmarks.")

        dtype = src.dtypes[0]
        if dtype == "uint8":
            warnings.append(
                "Input is 8-bit. Sentinel-2 L2A is normally 16-bit scaled reflectance; "
                "8-bit input has already been contrast-stretched, so spectral fidelity "
                "and spectral metrics will be degraded.")
        elif dtype.startswith("float"):
            sample = src.read(1, out_shape=(64, 64)).astype("float64")
            if np.nanmax(sample) <= 1.5:
                warnings.append("Input appears to be float reflectance already scaled to [0,1].")

        mapping, confident, band_warnings = identify_bands(src, filename)
        warnings.extend(band_warnings)

        bounds_wgs84 = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds))
        report = {
            "ok": True,
            "driver": src.driver,
            "width": src.width, "height": src.height,
            "band_count": src.count, "dtype": dtype,
            "crs": str(src.crs),
            "gsd_m": [round(gsd_x, 4), round(gsd_y, 4)],
            "bounds_native": [round(v, 3) for v in src.bounds],
            "bounds_wgs84": [round(v, 6) for v in bounds_wgs84],
            "transform": list(src.transform)[:6],
            "nodata": src.nodata,
            "has_overviews": bool(src.overviews(1)),
            "is_tiled": src.is_tiled,
            "compression": str(src.compression) if src.compression else None,
            "tags": {k: v for k, v in src.tags().items() if len(str(v)) < 200},
            "detected_band_mapping": mapping,
            "band_mapping_confident": confident,
            "band_descriptions": [d or None for d in (src.descriptions or [])],
            "warnings": warnings,
        }
    return report
