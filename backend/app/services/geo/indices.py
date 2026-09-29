"""Spectral indices (master plan §7J). Deterministic, explainable, no training."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio

NDVI_CLASSES = [(-1.0, 0.0, "Water / non-vegetated"), (0.0, 0.2, "Built-up / bare soil"),
                (0.2, 0.5, "Sparse vegetation"), (0.5, 1.0, "Dense vegetation")]
NDWI_CLASSES = [(-1.0, 0.0, "Land"), (0.0, 0.3, "Wet / mixed"), (0.3, 1.0, "Open water")]
INDEX_DEFS = {
    "ndvi": {"bands": ("B08", "B04"), "classes": NDVI_CLASSES, "colormap": "rdylgn",
             "label": "NDVI (vegetation)",
             "formula": "(B08 - B04) / (B08 + B04)"},
    "ndwi": {"bands": ("B03", "B08"), "classes": NDWI_CLASSES, "colormap": "magma",
             "label": "NDWI (water)",
             "formula": "(B03 - B08) / (B03 + B08)"},
    "ndbi": {"bands": ("B08", "B03"), "classes": None, "colormap": "magma",
             "label": "NDBI-proxy (built-up)",
             "formula": "(B03 - B08) / (B03 + B08)  [SWIR unavailable in the 10 m band set]"},
}


def _band_idx(names: list[str], want: str) -> int | None:
    return names.index(want) + 1 if want in names else None


def compute_index(src_path: Path, out_path: Path, index: str,
                  band_names: list[str]) -> dict[str, Any]:
    spec = INDEX_DEFS[index]
    a_name, b_name = spec["bands"]
    with rasterio.open(src_path) as src:
        ia, ib = _band_idx(band_names, a_name), _band_idx(band_names, b_name)
        if ia is None or ib is None:
            raise ValueError(f"{index.upper()} needs bands {a_name} and {b_name}; "
                             f"this output has {band_names}.")
        a = src.read(ia).astype("float64")
        b = src.read(ib).astype("float64")
        profile = src.profile.copy()
        nodata = src.nodata

    denom = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        val = np.where(np.abs(denom) > 1e-9, (a - b) / denom, np.nan)
    if nodata is not None:
        val = np.where((a == nodata) | (b == nodata), np.nan, val)
    val = np.clip(val, -1.0, 1.0)

    profile.update(driver="GTiff", count=1, dtype="float32", nodata=float("nan"),
                   tiled=True, blockxsize=512, blockysize=512, compress="deflate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(val.astype("float32")[None, :, :])
        dst.set_band_description(1, index.upper())
        dst.update_tags(INDEX=index.upper(), FORMULA=spec["formula"])

    return {"path": out_path, **_stats(val, spec, index)}


def _stats(val: np.ndarray, spec: dict, index: str) -> dict[str, Any]:
    finite = val[np.isfinite(val)]
    if finite.size == 0:
        return {"stats": {}, "classes": [], "pixel_count": 0}
    hist, edges = np.histogram(finite, bins=32, range=(-1, 1))
    classes = []
    if spec["classes"]:
        total = finite.size
        for lo, hi, label in spec["classes"]:
            n = int(((finite >= lo) & (finite < hi)).sum())
            classes.append({"label": label, "range": [lo, hi], "pixels": n,
                            "pct": round(100 * n / total, 2)})
    return {
        "stats": {"mean": round(float(finite.mean()), 4), "std": round(float(finite.std()), 4),
                  "min": round(float(finite.min()), 4), "max": round(float(finite.max()), 4),
                  "p25": round(float(np.percentile(finite, 25)), 4),
                  "median": round(float(np.median(finite)), 4),
                  "p75": round(float(np.percentile(finite, 75)), 4)},
        "histogram": {"bins": [round(float(e), 3) for e in edges], "counts": [int(c) for c in hist]},
        "classes": classes,
        "pixel_count": int(finite.size),
        "formula": spec["formula"], "label": spec["label"], "colormap": spec["colormap"],
    }


def compare_lr_sr(lr_stats: dict, sr_stats: dict, pixel_area_m2_lr: float,
                  pixel_area_m2_sr: float) -> dict[str, Any]:
    """
    The comparison that makes the analysis scientific rather than an unfalsifiable claim.

    "NDVI from SR" alone cannot be checked. "NDVI from 10 m vs from 2.5 m SR, and the
    delta" can be.
    """
    out: dict[str, Any] = {"mean_delta": None, "class_area_delta_km2": {}, "class_area_delta_pct": {}}
    if lr_stats.get("stats") and sr_stats.get("stats"):
        out["mean_delta"] = round(sr_stats["stats"]["mean"] - lr_stats["stats"]["mean"], 4)
    lr_by = {c["label"]: c for c in lr_stats.get("classes", [])}
    for c in sr_stats.get("classes", []):
        lr_c = lr_by.get(c["label"])
        if not lr_c:
            continue
        sr_km2 = c["pixels"] * pixel_area_m2_sr / 1e6
        lr_km2 = lr_c["pixels"] * pixel_area_m2_lr / 1e6
        out["class_area_delta_km2"][c["label"]] = round(sr_km2 - lr_km2, 4)
        out["class_area_delta_pct"][c["label"]] = round(c["pct"] - lr_c["pct"], 2)
    out["caveat"] = (
        "Indices computed on super-resolved bands inherit model-inferred detail. "
        "Compare against the low-resolution result before drawing conclusions.")
    return out
