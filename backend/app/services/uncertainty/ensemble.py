"""
Uncertainty — U1 ensemble disagreement (master plan §7H).

🚨 RULE 12, ABSOLUTE: uncertainty is NEVER derived from edge detection, a Laplacian,
local variance, or any other single-image trick. That would be fabricating a scientific
result. This module computes uncertainty ONLY from genuine disagreement between two or
more independent model outputs over the same input.

What this IS:  a relative indicator of where reconstruction is ambiguous.
What this IS NOT: a calibrated probability. `calibrated` is False until a reliability
diagram has been produced against ground truth (U5).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling

METHOD = "ensemble_disagreement"
CONFIDENCE_DEFINITION = (
    "1 minus the normalised mean per-pixel disagreement across the compared model "
    "outputs, clipped to [0,1]. This is a relative indicator of reconstruction "
    "ambiguity, not a calibrated probability, and it has not been validated against "
    "ground truth."
)
DISCLAIMER = (
    "Values show where the compared models disagree about reconstructed detail. They "
    "are NOT calibrated probabilities and have NOT been validated against ground truth."
)


def compute_ensemble_uncertainty(run_paths: dict[str, Path], out_path: Path,
                                 baseline_keys: set[str] | None = None) -> dict[str, Any] | None:
    """
    Per-pixel standard deviation across model outputs resampled to a common grid.

    Returns None when fewer than two outputs are available — the caller then reports
    `available: false, reason: NEEDS_MULTIPLE_RUNS` rather than inventing a map.
    """
    if len(run_paths) < 2:
        return None

    keys = list(run_paths)
    # Common grid = the COARSEST output (never upsample to invent precision).
    metas = {}
    for k, p in run_paths.items():
        with rasterio.open(p) as src:
            metas[k] = {"res": float(src.res[0]), "h": src.height, "w": src.width,
                        "crs": src.crs, "transform": src.transform,
                        "profile": src.profile.copy(), "count": src.count}
    grid_key = max(metas, key=lambda k: metas[k]["res"])
    gh, gw = metas[grid_key]["h"], metas[grid_key]["w"]
    n_bands = min(m["count"] for m in metas.values())

    stack = []
    for k in keys:
        with rasterio.open(run_paths[k]) as src:
            arr = src.read(indexes=list(range(1, n_bands + 1)),
                           out_shape=(n_bands, gh, gw),
                           resampling=Resampling.bilinear).astype("float64")
        stack.append(arr)
    cube = np.stack(stack)                       # (models, bands, h, w)

    # Disagreement = std across models, averaged over bands, normalised to reflectance.
    std = cube.std(axis=0).mean(axis=0)
    scale = max(float(np.nanpercentile(cube, 99)), 1.0)
    unc = (std / scale).astype("float32")

    profile = metas[grid_key]["profile"]
    profile.update(driver="GTiff", count=1, dtype="float32", nodata=float("nan"),
                   height=gh, width=gw, tiled=True, blockxsize=512, blockysize=512,
                   compress="deflate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(unc[None, :, :])
        dst.set_band_description(1, "ensemble_disagreement")
        dst.update_tags(METHOD=METHOD, CALIBRATED="false",
                        SOURCE_RUNS=",".join(keys), DISCLAIMER=DISCLAIMER)

    finite = unc[np.isfinite(unc)]
    p95 = float(np.percentile(finite, 95)) if finite.size else 0.0
    mean = float(finite.mean()) if finite.size else 0.0
    confidence = float(np.clip(1.0 - (mean / max(p95, 1e-6)) * 0.5, 0.0, 1.0))

    return {
        "path": out_path,
        "method": METHOD,
        "calibrated": False,
        "statistic": "per_pixel_std_of_reflectance",
        "unit": "reflectance (normalised)",
        "source_run_ids": keys,
        "compared_on_grid_gsd_m": round(metas[grid_key]["res"], 4),
        "bands_compared": n_bands,
        "includes_baseline": bool(baseline_keys and (set(keys) & baseline_keys)),
        "range": {
            "min": round(float(finite.min()), 6) if finite.size else 0.0,
            "max": round(float(finite.max()), 6) if finite.size else 0.0,
            "mean": round(mean, 6),
            "p50": round(float(np.percentile(finite, 50)), 6) if finite.size else 0.0,
            "p95": round(p95, 6),
        },
        "confidence_score": round(confidence, 3),
        "confidence_definition": CONFIDENCE_DEFINITION,
        "disclaimer": DISCLAIMER,
        "high_uncertainty_regions": _regions(unc, metas[grid_key]),
        "interpretation_note": _interpretation(baseline_keys, keys),
    }


def _interpretation(baseline_keys: set[str] | None, keys: list[str]) -> str:
    """If one 'model' is the bicubic baseline, this measures ADDED DETAIL, not uncertainty."""
    if baseline_keys and (set(keys) & baseline_keys) and len(keys) == 2:
        return ("One of the compared outputs is the bicubic baseline, so this map shows where "
                "the model ADDED DETAIL relative to simple interpolation. It is not a "
                "model-disagreement uncertainty map. Run two or more learned models for that.")
    return ("This map shows where the compared models disagree about reconstructed detail. "
            "High values mark areas where the reconstruction is ambiguous.")


def _regions(unc: np.ndarray, meta: dict, top_decile: float = 90.0, max_regions: int = 6):
    """Polygonise the highest-uncertainty areas — turns a heatmap into information."""
    try:
        from rasterio.features import shapes
        from rasterio.warp import transform_geom
    except Exception:
        return []
    finite = unc[np.isfinite(unc)]
    if finite.size == 0:
        return []
    thr = float(np.percentile(finite, top_decile))
    mask = (unc >= thr) & np.isfinite(unc)
    if not mask.any():
        return []

    px_area = meta["res"] ** 2
    out = []
    for geom, val in shapes(mask.astype("uint8"), mask=mask, transform=meta["transform"]):
        if val != 1:
            continue
        area_px = int(mask.sum())
        wgs = transform_geom(meta["crs"], "EPSG:4326", geom)
        out.append({"geometry": wgs,
                    "mean_uncertainty": round(float(unc[mask].mean()), 6),
                    "area_km2": round(area_px * px_area / 1e6, 5)})
        if len(out) >= max_regions:
            break
    return sorted(out, key=lambda r: -r["area_km2"])[:max_regions]
