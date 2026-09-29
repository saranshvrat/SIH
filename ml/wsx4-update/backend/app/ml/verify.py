"""
verify_output_contract — the gate between "the model ran" and "we may serve this".

Master plan §6.3: this is non-optional. It is the defence against silently wrong
science: a plausible-looking raster in the wrong place, at the wrong scale, with
swapped bands, or above the problem statement's <4 m requirement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from app.ml.contracts import InferenceResult
from app.ml.errors import OutputContractViolation

# PS 26142 requires "<4m". Encoded here so the problem statement is in the test suite.
PS_MAX_GSD_M = 4.0


def _fail(code: str, message: str, **details: Any) -> None:
    raise OutputContractViolation(message, {"check": code, **details})


def _bounds_within(inner, outer, tol_m: float) -> bool:
    return (
        inner.left >= outer.left - tol_m
        and inner.bottom >= outer.bottom - tol_m
        and inner.right <= outer.right + tol_m
        and inner.top <= outer.top + tol_m
    )


def verify_output_contract(result: InferenceResult, adapter, input_path: Path) -> dict[str, Any]:
    """Raise OutputContractViolation on any breach. Return the integrity report on success."""
    if not result.ok or result.sr_path is None:
        _fail("ADAPTER_NO_OUTPUT", f"Adapter returned no output: {result.error_message}")
    sr_path = Path(result.sr_path)
    if not sr_path.exists():
        _fail("ADAPTER_NO_OUTPUT", f"Declared output does not exist: {sr_path}")

    with rasterio.open(input_path) as src, rasterio.open(sr_path) as dst:
        # 1. CRS must survive inference untouched.
        if dst.crs != src.crs:
            _fail("CRS_MISMATCH", "Output CRS differs from input CRS.",
                  input_crs=str(src.crs), output_crs=str(dst.crs))

        # 2. Band count must match the adapter's own declaration.
        expected_bands = len(adapter.output_bands)
        if dst.count != expected_bands:
            _fail("BAND_COUNT_MISMATCH", "Output band count differs from the adapter declaration.",
                  expected=expected_bands, actual=dst.count)

        # 3. Scale factor must be what the adapter claims. Relative tolerance
        #    (2%), not absolute: some adapters (e.g. WSX4-SPATRAD, whose
        #    margin-based context padding doesn't divide evenly by its scale
        #    factor) have a few pixels of inherent, harmless rounding slop
        #    that becomes a smaller fraction of the ratio as input size grows.
        #    A genuinely wrong scale factor (e.g. adapter claims x4 but
        #    produced x8) is still caught — that's an order-of-magnitude
        #    difference, not a rounding artifact.
        actual_sf = src.res[0] / dst.res[0]
        declared_sf = adapter.scale_factor
        if declared_sf and abs(actual_sf - declared_sf) / declared_sf > 0.02:
            _fail("SCALE_FACTOR_MISMATCH", "Output scale factor differs from the declaration.",
                  declared=declared_sf, actual=round(actual_sf, 4))

        # 4. Footprint must not drift. Tolerance: one output pixel.
        if not _bounds_within(dst.bounds, src.bounds, tol_m=dst.res[0]):
            _fail("BOUNDS_DRIFT", "Output footprint is not contained within the input footprint.",
                  input_bounds=list(src.bounds), output_bounds=list(dst.bounds))

        # 5. The literal problem statement requirement.
        if dst.res[0] >= PS_MAX_GSD_M:
            _fail("GSD_ABOVE_PS_REQUIREMENT",
                  f"Output GSD {dst.res[0]:.2f} m does not meet the <{PS_MAX_GSD_M} m requirement.",
                  output_gsd_m=dst.res[0], required_below_m=PS_MAX_GSD_M)

        # 6. Output must contain actual data.
        probe = dst.read(1, out_shape=(min(64, dst.height), min(64, dst.width)))
        if np.all(np.isnan(probe)):
            _fail("OUTPUT_ALL_NAN", "Output band 1 is entirely NaN.")
        if float(np.nanmax(probe)) == float(np.nanmin(probe)):
            _fail("OUTPUT_CONSTANT", "Output band 1 is constant — inference likely failed silently.",
                  value=float(np.nanmin(probe)))

        report = {
            "crs_preserved": True,
            "crs": str(dst.crs),
            "bounds_match_input": True,
            "bounds_max_offset_m": round(
                max(abs(dst.bounds.left - src.bounds.left),
                    abs(dst.bounds.bottom - src.bounds.bottom),
                    abs(dst.bounds.right - src.bounds.right),
                    abs(dst.bounds.top - src.bounds.top)), 4),
            "scale_factor_actual": round(actual_sf, 4),
            "band_count_preserved": dst.count == src.count,
            "input_gsd_m": round(src.res[0], 4),
            "output_gsd_m": round(dst.res[0], 4),
            "meets_ps_requirement": dst.res[0] < PS_MAX_GSD_M,
            "contract_version": "0.1",
        }
    return report


def spectral_consistency_check(lr_path: Path, sr_path: Path, threshold: float = 0.02) -> dict:
    """
    Downsample SR back to the LR grid and compare per-band means.

    This is the platform's own evidence for the problem statement's "preserving
    spectral consistency" requirement. It needs no ground truth, so it runs on
    every job including real AOIs (master plan §7G).
    """
    from rasterio.enums import Resampling

    with rasterio.open(lr_path) as lr, rasterio.open(sr_path) as sr:
        back = sr.read(out_shape=(sr.count, lr.height, lr.width),
                       resampling=Resampling.average).astype("float64")
        ref = lr.read().astype("float64")
        n = min(back.shape[0], ref.shape[0])
        scale = max(float(np.nanmax(ref)), 1.0)
        per_band = {}
        for i in range(n):
            delta = float(np.nanmean(np.abs(back[i] - ref[i])) / scale)
            per_band[f"band_{i + 1}"] = round(delta, 6)

    worst = max(per_band.values()) if per_band else 0.0
    return {
        "method": "mean_abs_reflectance_delta_vs_LR",
        "per_band": per_band,
        "worst_band_delta": round(worst, 6),
        "threshold": threshold,
        "passed": worst <= threshold,
    }
