"""Shared adapter machinery: tiling, blending, georeferencing, COG-friendly writing."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio

from app.ml.contracts import InferenceRequest, InferenceResult


def cosine_window(h: int, w: int, taper: int) -> np.ndarray:
    """2D separable raised-cosine window. Feathers tile seams — the most visible artefact."""
    def ramp(n: int) -> np.ndarray:
        v = np.ones(n, dtype=np.float32)
        t = min(taper, n // 2)
        if t > 0:
            edge = 0.5 * (1 - np.cos(np.linspace(0, np.pi, t, dtype=np.float32)))
            v[:t] = edge
            v[-t:] = edge[::-1]
        return v
    return (ramp(h)[:, None] * ramp(w)[None, :])[None, :, :]


def tiled_apply(
    arr: np.ndarray,
    scale_factor: int,
    tile_px: int,
    overlap_px: int,
    fn: Callable[[np.ndarray], np.ndarray],
    progress_cb: Callable[[float, str], None] | None = None,
) -> tuple[np.ndarray, int]:
    """
    Run `fn` over overlapping tiles of a (C,H,W) array and blend the results.

    `fn` maps (C, tile, tile) -> (C, tile*sf, tile*sf). Blending uses a cosine window
    so adjacent tiles fade into one another instead of leaving hard edges.
    """
    c, h, w = arr.shape
    sf = scale_factor
    tile = min(tile_px, h, w)
    step = max(1, tile - 2 * overlap_px)

    out = np.zeros((c, h * sf, w * sf), dtype=np.float32)
    acc = np.zeros((1, h * sf, w * sf), dtype=np.float32)

    ys = sorted({min(y, max(0, h - tile)) for y in range(0, h, step)})
    xs = sorted({min(x, max(0, w - tile)) for x in range(0, w, step)})
    coords = [(y, x) for y in ys for x in xs]

    for i, (y0, x0) in enumerate(coords):
        patch = arr[:, y0:y0 + tile, x0:x0 + tile]
        pred = np.asarray(fn(patch), dtype=np.float32)
        ph, pw = pred.shape[1], pred.shape[2]
        win = cosine_window(ph, pw, taper=overlap_px * sf)
        oy, ox = y0 * sf, x0 * sf
        out[:, oy:oy + ph, ox:ox + pw] += pred * win
        acc[:, oy:oy + ph, ox:ox + pw] += win
        if progress_cb:
            progress_cb((i + 1) / len(coords), f"tile {i + 1}/{len(coords)}")

    return out / np.maximum(acc, 1e-8), len(coords)


def write_georeferenced(
    out_path: Path,
    array: np.ndarray,
    src_profile: dict,
    src_transform,
    src_crs,
    scale_factor: int,
    band_names: list[str],
    tags: dict[str, Any],
    dtype: str,
) -> float:
    """
    Re-attach the geospatial reference to a bare model output array.

    Research code almost always returns a plain array (master plan §13.2 B11), so
    this is where CRS, affine transform, band descriptions and provenance are
    restored. Doing it in one shared place means no adapter can forget.
    """
    out_transform = src_transform @ src_transform.scale(1 / scale_factor, 1 / scale_factor)
    profile = dict(src_profile)
    info = np.iinfo(np.dtype(dtype)) if np.issubdtype(np.dtype(dtype), np.integer) else None
    data = np.clip(array, info.min, info.max) if info is not None else array

    profile.update(
        driver="GTiff", dtype=dtype, count=array.shape[0],
        height=array.shape[1], width=array.shape[2],
        transform=out_transform, crs=src_crs,
        tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=2,
    )
    profile.pop("nodata", None) if profile.get("nodata") is None else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data.astype(dtype))
        for i, name in enumerate(band_names[: array.shape[0]], start=1):
            dst.set_band_description(i, name)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
    return abs(out_transform.a)


class BaseAdapter:
    """Default no-op lifecycle. Adapters override what they need."""

    key: str = "base"
    display_name: str = "Base"
    version: str = "0.0.0"
    scale_factor: int = 4
    input_bands: list[str] = ["B02", "B03", "B04", "B08"]
    output_bands: list[str] = ["B02", "B03", "B04", "B08"]
    input_gsd_m: float = 10.0
    output_gsd_m: float = 2.5
    requires_gpu: bool = False
    supports_uncertainty: bool = False
    supports_sampling: bool = False
    is_baseline: bool = False
    max_input_px: int = 512
    default_tile_px: int = 128
    tile_overlap_px: int = 16

    def load(self, device: str = "cuda") -> None:
        return None

    def is_loaded(self) -> bool:
        return True

    def unload(self) -> None:
        return None

    def healthcheck(self) -> dict[str, Any]:
        return {"available": True}

    def infer(self, req: InferenceRequest) -> InferenceResult:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _fail(code: str, msg: str, t0: float | None = None) -> InferenceResult:
        return InferenceResult(
            ok=False, sr_path=None, error_code=code, error_message=msg,
            runtime_s=round(time.perf_counter() - t0, 3) if t0 else 0.0,
        )
