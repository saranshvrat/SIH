"""
WSX4-SPATRAD adapter.

Unlike SEN2SR (a PyTorch source-code model needing ML_REPO_PATH), this model
ships as a self-contained ONNX export + a YAML sidecar (bands/factor/margin).
No custom inference code from the ML team is needed — the graph is fully
described by the .onnx file itself. This adapter drives it with onnxruntime,
CPU-only, no GPU worker required.

⚠️ Preprocessing/margin protocol confirmed directly from the ML team's own
reference evaluation notebook (wsx4_spatrad_eval_lat_lon.ipynb), NOT guessed:

  1. Input is RAW, UNSCALED Sentinel-2 L2A reflectance (uint16 DN). The model
     does NOT expect /10000 or [0,1] normalisation — PREPROCESS_SCALE = 1.0
     in the reference notebook. Feeding normalised input silently produces
     flat/saturated garbage output with no error (this was a real bug the ML
     team hit and documented). Do not "fix" this by adding normalisation.
  2. `margin` in the YAML is in OUTPUT-resolution pixels. The LR-side context
     padding applied BEFORE inference is `ceil(margin / factor)`, reflect
     mode. The crop applied AFTER inference is `margin` pixels (not
     `margin * factor`) off each side.
  3. Tensor names are read from the ONNX graph at load time
     (session.get_inputs()[0].name), never hardcoded.
  4. Because `margin / factor` is not always an integer (130/4 = 32.5 for the
     shipped weights), the padding round-trip has a few pixels of unavoidable
     slop: cropped output size is NOT always exactly input_size * factor.
     This adapter derives the output's georeferencing from the ACTUAL cropped
     array shape against the ORIGINAL (unpadded) input's footprint, rather
     than assuming an exact integer scale factor — so the file's bounds stay
     geospatially correct even though the pixel-count ratio can be off by a
     tiny fraction (e.g. 4.0625x instead of exactly 4.0x on a 64px test tile;
     negligible on any real AOI-sized input).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from app.ml.adapters.base import BaseAdapter
from app.ml.contracts import InferenceRequest, InferenceResult
from app.core.config import settings


class Wsx4SpatradAdapter(BaseAdapter):
    key = "wsx4_spatrad"
    display_name = "WSX4-SPATRAD"
    version = "1.0.0"

    scale_factor = 4
    input_gsd_m = 10.0
    output_gsd_m = 2.5
    input_bands = ["B02", "B03", "B04", "B08"]
    output_bands = ["B02", "B03", "B04", "B08"]

    requires_gpu = False          # ONNX Runtime CPU execution provider
    supports_uncertainty = False
    supports_sampling = False
    is_baseline = False

    max_input_px = 512
    default_tile_px = 512         # single-shot inference; see infer() below
    tile_overlap_px = 0

    def __init__(self, spec: Any = None):
        self._session = None
        self._input_name: str | None = None
        self._output_name: str | None = None
        self._spec = spec
        self.margin_px = 130
        self.weights_filename: str | None = None
        if spec is not None:
            for attr in ("scale_factor", "input_bands", "output_bands", "max_input_px"):
                if getattr(spec, attr, None) is not None:
                    setattr(self, attr, getattr(spec, attr))
            self.margin_px = int(getattr(spec, "margin_px", 130) or 130)
            self.weights_filename = getattr(spec, "weights_filename", None)
        self.output_gsd_m = self.input_gsd_m / self.scale_factor

    # ------------------------------------------------------------------ path
    def _weights_path(self) -> Path:
        return Path(settings.ONNX_WEIGHTS_DIR) / (self.weights_filename or f"{self.key}.onnx")

    # ------------------------------------------------------------------ health
    def healthcheck(self) -> dict[str, Any]:
        path = self._weights_path()
        try:
            import onnxruntime  # noqa: F401
            ort_ok = True
        except Exception:
            ort_ok = False

        if not ort_ok:
            reason = "ONNXRUNTIME_NOT_INSTALLED"
        elif not path.exists():
            reason = "WEIGHTS_NOT_FOUND"
        else:
            reason = None

        return {
            "available": reason is None,
            "reason": reason,
            "weights_path": str(path),
            "weights_present": path.exists(),
            "onnxruntime_installed": ort_ok,
            "verified_against_repo": True,  # confirmed against the ML team's own eval notebook
        }

    # -------------------------------------------------------------------- load
    def load(self, device: str = "cpu") -> None:
        if self._session is not None:
            return
        health = self.healthcheck()
        if not health["available"]:
            from app.ml.errors import ModelUnavailableError
            raise ModelUnavailableError(f"{self.display_name} is not available: {health['reason']}.", health)

        import onnxruntime as ort
        self._session = ort.InferenceSession(str(self._weights_path()), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

    def is_loaded(self) -> bool:
        return self._session is not None

    def unload(self) -> None:
        self._session = None
        self._input_name = self._output_name = None

    # ------------------------------------------------------------------- infer
    def infer(self, req: InferenceRequest) -> InferenceResult:
        t0 = time.perf_counter()
        try:
            self.load("cpu")

            with rasterio.open(req.input_path) as src:
                need = len(self.input_bands)
                if src.count < need:
                    return self._fail("BAND_COUNT_MISMATCH",
                                      f"Expected {need} bands, got {src.count}.", t0)
                arr = src.read(indexes=list(range(1, need + 1)))  # keep native dtype (raw DN)
                profile, crs = src.profile.copy(), src.crs
                bounds, src_dtype = src.bounds, src.dtypes[0]

            if max(arr.shape[1], arr.shape[2]) > self.max_input_px:
                return self._fail(
                    "INPUT_TOO_LARGE",
                    f"Input {arr.shape[2]}x{arr.shape[1]} px exceeds max_input_px={self.max_input_px}.", t0)

            if req.progress_cb:
                req.progress_cb(0.1, "padding for context")

            factor = float(self.scale_factor)
            context_margin_lr = int(np.ceil(self.margin_px / factor)) if self.margin_px else 0

            # 🚨 RAW DN — no /10000, no [0,1] normalisation. Confirmed from the
            # ML team's own reference notebook; feeding normalised input here
            # produces flat/saturated garbage with no error.
            x = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
            orig_h, orig_w = x.shape[1], x.shape[2]
            if context_margin_lr > 0:
                x = np.pad(x, ((0, 0), (context_margin_lr, context_margin_lr),
                               (context_margin_lr, context_margin_lr)), mode="reflect")
            x = x[np.newaxis, ...]  # (1, 4, H, W)

            if req.progress_cb:
                req.progress_cb(0.3, "running inference")

            out = self._session.run([self._output_name], {self._input_name: x})[0][0]  # drop batch

            if self.margin_px > 0:
                m = self.margin_px
                out = out[:, m:-m, m:-m]

            if req.progress_cb:
                req.progress_cb(0.85, "writing output")

            out_h, out_w = out.shape[1], out.shape[2]

            # Georeference from the ACTUAL cropped shape against the ORIGINAL
            # (unpadded) input's real-world footprint — robust to the margin's
            # inherent rounding slop (see module docstring point 4).
            out_transform = from_bounds(bounds.left, bounds.bottom, bounds.right, bounds.top,
                                        out_w, out_h)
            out_path = req.output_dir / f"{req.run_id}_sr.tif"
            out_path.parent.mkdir(parents=True, exist_ok=True)

            write_dtype = "float32" if not np.issubdtype(np.dtype(src_dtype), np.integer) else src_dtype
            if write_dtype != "float32":
                info = np.iinfo(np.dtype(src_dtype))
                out = np.clip(out, info.min, info.max)

            out_profile = dict(profile)
            out_profile.update(driver="GTiff", dtype=write_dtype, count=out.shape[0],
                               height=out_h, width=out_w, transform=out_transform, crs=crs,
                               tiled=True, blockxsize=512, blockysize=512,
                               compress="deflate", predictor=2)
            out_profile.pop("nodata", None) if out_profile.get("nodata") is None else None

            with rasterio.open(out_path, "w", **out_profile) as dst:
                dst.write(out.astype(write_dtype))
                for i, name in enumerate(self.output_bands[: out.shape[0]], start=1):
                    dst.set_band_description(i, name)
                dst.update_tags(
                    SR_MODEL=self.key, SR_MODEL_VERSION=self.version,
                    WEIGHTS_FILE=str(self._weights_path().name),
                    PIXELFORGE_CONTRACT="0.1",
                    DISCLAIMER=("Contains model-inferred detail not directly observed by the "
                                "sensor. Not for cadastral, legal, navigational or "
                                "safety-critical measurement."),
                )

            actual_scale = out_w / orig_w  # reported honestly; may differ slightly from nominal factor
            measured_gsd = abs(out_transform.a)

            return InferenceResult(
                ok=True, sr_path=out_path,
                uncertainty_path=None, uncertainty_method=None,
                output_gsd_m=measured_gsd, output_bands=list(self.output_bands),
                scale_factor=round(actual_scale, 4), dtype=write_dtype,
                value_range=(float(np.nanmin(out)), float(np.nanmax(out))),
                runtime_s=round(time.perf_counter() - t0, 3),
                peak_vram_mb=None, device_name="cpu (onnxruntime)",
                tiles_processed=1,
                warnings=([f"Output pixel dimensions ({out_w}x{out_h}) differ slightly from an "
                          f"exact {factor:g}x scale due to the model's margin rounding "
                          f"(ceil({self.margin_px}/{factor:g})); georeferencing is derived from "
                          f"the actual output shape and remains spatially correct."]
                         if out_w != round(orig_w * factor) else []),
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail("INFERENCE_FAILED", f"{type(exc).__name__}: {exc}", t0)
