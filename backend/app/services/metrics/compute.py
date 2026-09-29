"""
Quality assessment (master plan §7G).

Two families:
  full_reference  — PSNR/SSIM/SAM/RMSE/LPIPS. Need ground truth. VALIDATION MODE ONLY.
  no_reference    — LR-consistency, spectral angle vs LR, edge energy. Work on any AOI.

🚨 In inference mode, full_reference is `None` with an explanation. It is NEVER a
placeholder, never a zero, and never a number from a slide.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling

IMPLEMENTATION = "pixelforge.metrics@0.1.0"

EXPLANATIONS = {
    "psnr": "Peak signal-to-noise ratio in dB against the reference. Higher is better. "
            "Sensitive to pixel-wise error but not to perceptual quality.",
    "ssim": "Structural similarity (luminance, contrast, structure) against the reference, "
            "0 to 1. Higher is better. Closer to human judgement of structure than PSNR.",
    "sam": "Spectral Angle Mapper: the angle in degrees between the predicted and reference "
           "spectral vectors at each pixel. Lower is better. This is the primary evidence "
           "that spectral consistency was preserved.",
    "rmse": "Root mean squared error in reflectance units against the reference. Lower is better.",
    "lpips": "Learned perceptual distance. Lower is better. Note that a generative model can "
             "score well here while inventing detail, so read it alongside SAM and PSNR.",
    "lr_consistency_psnr": "The enhanced image downsampled back to 10 m and compared with the "
                           "original observation. High values mean the model did not alter what "
                           "the satellite actually measured. Requires no ground truth.",
    "lr_consistency_ssim": "Structural agreement between the downsampled output and the original "
                           "10 m observation. Requires no ground truth.",
    "spectral_angle_vs_lr": "Spectral angle in degrees between the downsampled output and the "
                            "original observation. Lower means better spectral preservation.",
    "edge_energy_ratio": "How much high-frequency detail the model added, relative to simple "
                         "upsampling. This is neither good nor bad on its own: more added detail "
                         "may be real reconstruction or hallucination. Read it with the "
                         "uncertainty map.",
}


def _read(path: Path, out_shape=None) -> tuple[np.ndarray, Any]:
    with rasterio.open(path) as src:
        arr = (src.read(out_shape=(src.count, *out_shape), resampling=Resampling.average)
               if out_shape else src.read()).astype("float64")
        return arr, src


def _align(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = min(a.shape[0], b.shape[0])
    h = min(a.shape[1], b.shape[1])
    w = min(a.shape[2], b.shape[2])
    return a[:c, :h, :w], b[:c, :h, :w]


def psnr(pred: np.ndarray, ref: np.ndarray, data_range: float) -> float:
    mse = float(np.nanmean((pred - ref) ** 2))
    if mse <= 1e-12:
        return float("inf")
    return float(20 * np.log10(data_range) - 10 * np.log10(mse))


def rmse(pred: np.ndarray, ref: np.ndarray) -> float:
    return float(np.sqrt(np.nanmean((pred - ref) ** 2)))


def ssim(pred: np.ndarray, ref: np.ndarray, data_range: float, win: int = 7) -> float:
    """Gaussian-free uniform-window SSIM (Wang et al. 2004), averaged over bands."""
    c1, c2 = (0.01 * data_range) ** 2, (0.03 * data_range) ** 2
    vals = []
    for i in range(pred.shape[0]):
        x, y = pred[i], ref[i]
        k = np.ones((win, win)) / (win * win)
        mu_x, mu_y = _conv2(x, k), _conv2(y, k)
        xx, yy, xy = _conv2(x * x, k), _conv2(y * y, k), _conv2(x * y, k)
        sx = xx - mu_x**2
        sy = yy - mu_y**2
        sxy = xy - mu_x * mu_y
        num = (2 * mu_x * mu_y + c1) * (2 * sxy + c2)
        den = (mu_x**2 + mu_y**2 + c1) * (sx + sy + c2)
        vals.append(float(np.nanmean(num / np.maximum(den, 1e-12))))
    return float(np.mean(vals))


def _conv2(a: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    kh, kw = kernel.shape
    pad = ((kh // 2, kh // 2), (kw // 2, kw // 2))
    p = np.pad(a, pad, mode="reflect")
    out = np.zeros_like(a, dtype="float64")
    for i in range(kh):
        for j in range(kw):
            out += kernel[i, j] * p[i:i + a.shape[0], j:j + a.shape[1]]
    return out


def sam_degrees(pred: np.ndarray, ref: np.ndarray) -> float:
    """Spectral Angle Mapper — the spectral-consistency metric the PS asks for."""
    p = pred.reshape(pred.shape[0], -1)
    r = ref.reshape(ref.shape[0], -1)
    dot = np.nansum(p * r, axis=0)
    norm = np.sqrt(np.nansum(p**2, axis=0)) * np.sqrt(np.nansum(r**2, axis=0))
    valid = norm > 1e-9
    if not valid.any():
        return float("nan")
    cos = np.clip(dot[valid] / norm[valid], -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)).mean())


def _edge_energy(a: np.ndarray) -> float:
    gy = np.abs(np.diff(a, axis=-2)).mean()
    gx = np.abs(np.diff(a, axis=-1)).mean()
    return float((gx + gy) / 2)


def compute_no_reference(lr_path: Path, sr_path: Path) -> dict[str, Any]:
    """Metrics that need no ground truth — available on EVERY real AOI."""
    with rasterio.open(lr_path) as lr_src:
        lr = lr_src.read().astype("float64")
        lr_h, lr_w = lr_src.height, lr_src.width
    with rasterio.open(sr_path) as sr_src:
        sr_down = sr_src.read(out_shape=(sr_src.count, lr_h, lr_w),
                              resampling=Resampling.average).astype("float64")
        sr_full = sr_src.read().astype("float64")

    sr_down, lr_a = _align(sr_down, lr)
    dr = max(float(np.nanmax(lr_a)) - float(np.nanmin(lr_a)), 1.0)

    # Reference for edge energy: bicubic-equivalent upsample of the LR.
    lr_up = np.repeat(np.repeat(lr_a, sr_full.shape[1] // max(lr_a.shape[1], 1), axis=1),
                      sr_full.shape[2] // max(lr_a.shape[2], 1), axis=2)
    e_sr = _edge_energy(sr_full)
    e_lr = _edge_energy(lr_up) or 1e-9

    return {
        "lr_consistency_psnr": {"value": round(psnr(sr_down, lr_a, dr), 3), "unit": "dB"},
        "lr_consistency_ssim": {"value": round(ssim(sr_down, lr_a, dr), 4), "unit": None},
        "spectral_angle_vs_lr": {"value": round(sam_degrees(sr_down, lr_a), 4), "unit": "deg"},
        "edge_energy_ratio": {"value": round(e_sr / e_lr, 4), "unit": None},
    }


def compute_full_reference(sr_path: Path, ref_path: Path) -> dict[str, Any]:
    """VALIDATION MODE ONLY. Requires genuine high-resolution ground truth."""
    with rasterio.open(ref_path) as ref_src:
        ref_h, ref_w, ref_count = ref_src.height, ref_src.width, ref_src.count
        ref = ref_src.read().astype("float64")
    with rasterio.open(sr_path) as sr_src:
        sr = (sr_src.read(out_shape=(sr_src.count, ref_h, ref_w), resampling=Resampling.bilinear)
              if (sr_src.height, sr_src.width) != (ref_h, ref_w) else sr_src.read()).astype("float64")

    sr, ref = _align(sr, ref)
    dr = max(float(np.nanmax(ref)) - float(np.nanmin(ref)), 1.0)

    per_band = {}
    names = ["B02", "B03", "B04", "B08"]
    for i in range(sr.shape[0]):
        per_band[names[i] if i < len(names) else f"band_{i+1}"] = {
            "psnr": round(psnr(sr[i:i+1], ref[i:i+1], dr), 3),
            "rmse": round(rmse(sr[i:i+1], ref[i:i+1]), 4),
        }

    return {
        "psnr": {"value": round(psnr(sr, ref, dr), 3), "unit": "dB"},
        "ssim": {"value": round(ssim(sr, ref, dr), 4), "unit": None},
        "sam": {"value": round(sam_degrees(sr, ref), 4), "unit": "deg"},
        "rmse": {"value": round(rmse(sr, ref), 4), "unit": "reflectance"},
        # 🚨 LPIPS needs a pretrained network. We do NOT approximate it. Null + reason.
        "lpips": {"value": None, "unit": None,
                  "unavailable_reason": "LPIPS requires torch and pretrained AlexNet weights, "
                                        "which are not installed in this worker."},
        "per_band": per_band,
        "implementation": IMPLEMENTATION,
    }


def delta_vs_baseline(model: dict, baseline: dict) -> dict[str, Any]:
    """
    "+3.3 dB over bicubic on this tile" persuades. A bare "PSNR 27.3" does not.
    """
    higher_is_better = {"psnr", "ssim", "lr_consistency_psnr", "lr_consistency_ssim"}
    out: dict[str, Any] = {}
    for name, m in model.items():
        b = baseline.get(name)
        if not isinstance(m, dict) or not isinstance(b, dict):
            continue
        if m.get("value") is None or b.get("value") is None:
            continue
        if not np.isfinite(m["value"]) or not np.isfinite(b["value"]):
            continue
        d = m["value"] - b["value"]
        out[name] = {"delta": round(d, 4),
                     "improved": (d > 0) if name in higher_is_better else (d < 0),
                     "baseline_value": b["value"]}
    return out
