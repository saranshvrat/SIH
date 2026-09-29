"""
Bicubic baseline adapter.

Master plan §7E.1 calls this "the highest value-per-line-of-code in the entire project":
it gives every metric a baseline, makes comparison possible before a second real model
exists, runs on CPU so the demo survives a dead GPU, and produces the single most
persuasive visual in the presentation.

It adds NO new information. That is the point, and the UI says so.
"""
from __future__ import annotations

import os
import time
from typing import Any

import numpy as np
import rasterio

from app.ml.adapters.base import BaseAdapter, tiled_apply, write_georeferenced
from app.ml.contracts import InferenceRequest, InferenceResult


def _cubic_weights(positions: np.ndarray):
    """Catmull-Rom (a=-0.5) cubic convolution weights for fractional sample positions."""
    base = np.floor(positions).astype(int)
    d = (positions - base)[:, None]
    w = np.concatenate([
        -0.5 * d**3 + d**2 - 0.5 * d,
        1.5 * d**3 - 2.5 * d**2 + 1.0,
        -1.5 * d**3 + 2.0 * d**2 + 0.5 * d,
        0.5 * d**3 - 0.5 * d**2,
    ], axis=1)
    return base, w


def _bicubic_upsample(patch: np.ndarray, sf: int) -> np.ndarray:
    """Bicubic upsample of a (C,H,W) array by an integer factor. Pure NumPy."""
    c, h, w = patch.shape
    out_h, out_w = h * sf, w * sf
    src = patch.astype(np.float64)

    # Cell-centre aligned source positions (avoids the half-pixel shift that
    # would silently break georeferencing alignment against the SR output).
    ys = (np.arange(out_h, dtype=np.float64) + 0.5) / sf - 0.5
    xs = (np.arange(out_w, dtype=np.float64) + 0.5) / sf - 0.5
    by, wy = _cubic_weights(ys)
    bx, wx = _cubic_weights(xs)

    # Vertical pass: (C,H,W) -> (C,out_h,W)
    tmp = np.zeros((c, out_h, w), dtype=np.float64)
    for k in range(4):
        rows = np.clip(by + (k - 1), 0, h - 1)
        tmp += src[:, rows, :] * wy[:, k][None, :, None]

    # Horizontal pass: (C,out_h,W) -> (C,out_h,out_w)
    out = np.zeros((c, out_h, out_w), dtype=np.float64)
    for k in range(4):
        cols = np.clip(bx + (k - 1), 0, w - 1)
        out += tmp[:, :, cols] * wx[:, k][None, None, :]

    return out.astype(np.float32)


class BicubicAdapter(BaseAdapter):
    key = "bicubic"
    display_name = "Bicubic (baseline)"
    version = "1.0.0"
    scale_factor = 4
    input_bands = ["B02", "B03", "B04", "B08"]
    output_bands = ["B02", "B03", "B04", "B08"]
    input_gsd_m = 10.0
    output_gsd_m = 2.5
    requires_gpu = False
    supports_uncertainty = False
    supports_sampling = False
    is_baseline = True
    max_input_px = 2048
    default_tile_px = 256
    tile_overlap_px = 16

    def __init__(self, spec: Any = None):
        self._spec = spec
        if spec is not None:
            for attr in ("scale_factor", "input_bands", "output_bands", "max_input_px"):
                if getattr(spec, attr, None) is not None:
                    setattr(self, attr, getattr(spec, attr))
        self.output_gsd_m = self.input_gsd_m / self.scale_factor

    def healthcheck(self) -> dict[str, Any]:
        return {"available": True, "reason": None, "requires_gpu": False,
                "weights_present": True, "notes": "Classical interpolation; no weights required."}

    def infer(self, req: InferenceRequest) -> InferenceResult:
        t0 = time.perf_counter()
        try:
            with rasterio.open(req.input_path) as src:
                if src.count < len(self.input_bands):
                    return self._fail(
                        "BAND_COUNT_MISMATCH",
                        f"Expected at least {len(self.input_bands)} bands, got {src.count}.", t0)
                arr = src.read(indexes=list(range(1, len(self.input_bands) + 1))).astype(np.float32)
                profile, crs, transform = src.profile.copy(), src.crs, src.transform
                src_dtype = src.dtypes[0]

            if max(arr.shape[1], arr.shape[2]) > self.max_input_px:
                return self._fail(
                    "INPUT_TOO_LARGE",
                    f"Input {arr.shape[2]}x{arr.shape[1]} px exceeds max_input_px={self.max_input_px}.", t0)

            sr, n_tiles = tiled_apply(
                arr, self.scale_factor, req.tile_px or self.default_tile_px,
                req.tile_overlap_px, lambda p: _bicubic_upsample(p, self.scale_factor),
                req.progress_cb,
            )

            out_path = req.output_dir / f"{req.run_id}_sr.tif"
            gsd = write_georeferenced(
                out_path, sr, profile, transform, crs, self.scale_factor,
                self.output_bands,
                tags={
                    "SR_MODEL": self.key, "SR_MODEL_VERSION": self.version,
                    "ML_COMMIT": os.getenv("ML_COMMIT", "n/a-baseline"),
                    "PIXELFORGE_CONTRACT": "0.1",
                    "IS_BASELINE": "true",
                    "DISCLAIMER": ("Bicubic interpolation baseline. Adds no information beyond "
                                   "the original observation; provided for comparison only."),
                },
                dtype=src_dtype,
            )

            return InferenceResult(
                ok=True, sr_path=out_path,
                uncertainty_path=None, uncertainty_method=None,
                output_gsd_m=gsd, output_bands=list(self.output_bands),
                scale_factor=float(self.scale_factor), dtype=src_dtype,
                value_range=(float(np.nanmin(sr)), float(np.nanmax(sr))),
                runtime_s=round(time.perf_counter() - t0, 3),
                peak_vram_mb=None, device_name="cpu", tiles_processed=n_tiles,
                warnings=["Baseline interpolation: no new detail is reconstructed."],
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary must not leak
            return self._fail("INFERENCE_FAILED", f"{type(exc).__name__}: {exc}", t0)
