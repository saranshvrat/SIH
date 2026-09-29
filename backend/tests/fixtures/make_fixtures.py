"""
Synthetic Sentinel-2-like fixtures. No network, no ML repo, fully deterministic.

Critical fixture: `make_band_identifiable()` writes a raster where each band is a distinct
constant. Any adapter that silently reorders bands is caught by comparing band means —
the failure mode the master plan calls a "silent killer" (§7C step 2).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

# A real Sentinel-2 UTM zone over eastern India (tile 45Q). Durgapur-ish.
DEFAULT_CRS = "EPSG:32645"
DEFAULT_ORIGIN = (712340.0, 2605120.0)
DEFAULT_GSD = 10.0
S2_BANDS = ["B02", "B03", "B04", "B08"]


def _write(path: Path, data: np.ndarray, gsd: float, crs: str, origin, band_names, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    transform = from_origin(origin[0], origin[1], gsd, gsd)
    profile = dict(driver="GTiff", height=data.shape[1], width=data.shape[2],
                   count=data.shape[0], dtype=data.dtype, crs=crs, transform=transform,
                   tiled=True, blockxsize=256, blockysize=256, compress="deflate")
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        for i, n in enumerate(band_names[: data.shape[0]], 1):
            dst.set_band_description(i, n)
        dst.update_tags(PRODUCT_TYPE="L2A-SYNTHETIC", SENSED_AT="2026-02-14T05:12:31Z")
    return path


def make_scene(path: Path, size: int = 256, gsd: float = DEFAULT_GSD,
               crs: str = DEFAULT_CRS, origin=DEFAULT_ORIGIN, seed: int = 7) -> Path:
    """Structured, non-random imagery: fields, a river, a road grid, a built-up block."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)

    fields = (np.sin(xx / 17.0) * np.cos(yy / 23.0) > 0.1).astype(np.float32)
    river = (np.abs(yy - (size * 0.6 + 18 * np.sin(xx / 30.0))) < 4).astype(np.float32)
    roads = (((xx.astype(int) % 48) < 2) | ((yy.astype(int) % 61) < 2)).astype(np.float32)
    built = ((xx > size * 0.62) & (yy < size * 0.34)).astype(np.float32)
    texture = rng.normal(0, 40, (size, size)).astype(np.float32)

    b02 = 600 + 250 * built + 120 * roads - 120 * river + 0.4 * texture
    b03 = 800 + 220 * built + 140 * roads + 200 * fields - 60 * river + 0.4 * texture
    b04 = 900 + 400 * built + 180 * roads - 260 * fields - 150 * river + 0.4 * texture
    b08 = 2200 + 1800 * fields - 300 * built - 1800 * river + 0.6 * texture

    data = np.stack([b02, b03, b04, b08])
    data = np.clip(data, 1, 10000).astype("uint16")
    return _write(path, data, gsd, crs, origin, S2_BANDS)


def make_band_identifiable(path: Path, size: int = 128, values=(1000, 2000, 3000, 4000)) -> Path:
    """Each band a distinct constant + tiny gradient, so band order is provable."""
    bands = []
    for v in values:
        g = np.linspace(0, 20, size, dtype=np.float32)[None, :].repeat(size, axis=0)
        bands.append(np.full((size, size), float(v), dtype=np.float32) + g)
    return _write(path, np.stack(bands).astype("uint16"), DEFAULT_GSD, DEFAULT_CRS,
                  DEFAULT_ORIGIN, S2_BANDS)


def make_paired_lr_hr(lr_path: Path, hr_path: Path, lr_size: int = 64, sf: int = 4):
    """A validation-mode pair: HR truth, and its honest average-pooled LR counterpart."""
    from rasterio.enums import Resampling
    hr = make_scene(hr_path, size=lr_size * sf, gsd=DEFAULT_GSD / sf)
    with rasterio.open(hr) as src:
        lr_data = src.read(out_shape=(src.count, lr_size, lr_size),
                           resampling=Resampling.average).astype("uint16")
    _write(lr_path, lr_data, DEFAULT_GSD, DEFAULT_CRS, DEFAULT_ORIGIN, S2_BANDS)
    return lr_path, hr_path


def make_invalid(kind: str, path: Path) -> Path:
    """Fixtures for every upload rejection path (master plan §11 Phase 6)."""
    size = 64
    data = np.random.default_rng(1).integers(1, 5000, (4, size, size)).astype("uint16")
    if kind == "no_crs":
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", driver="GTiff", height=size, width=size, count=4,
                           dtype="uint16") as dst:
            dst.write(data)
        return path
    if kind == "wrong_gsd":
        return _write(path, data, 30.0, DEFAULT_CRS, DEFAULT_ORIGIN, S2_BANDS)
    if kind == "too_few_bands":
        return _write(path, data[:3], DEFAULT_GSD, DEFAULT_CRS, DEFAULT_ORIGIN, S2_BANDS[:3])
    if kind == "uint8":
        return _write(path, (data // 40).astype("uint8"), DEFAULT_GSD, DEFAULT_CRS,
                      DEFAULT_ORIGIN, S2_BANDS)
    if kind == "not_a_raster":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ\x90\x00\x03" + b"\x00" * 512)  # renamed executable
        return path
    if kind == "unnamed_bands":
        return _write(path, data, DEFAULT_GSD, DEFAULT_CRS, DEFAULT_ORIGIN, ["", "", "", ""])
    raise ValueError(kind)
