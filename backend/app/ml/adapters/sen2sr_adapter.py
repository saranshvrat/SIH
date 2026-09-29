"""
SEN2SR adapter.

⚠️  STATUS: BLOCKED:ML-REPO (see docs/DECISIONS.md ADR-004)

    The PixelForge ML repository was NOT available when this adapter was written, so
    the exact inference entrypoint could not be inspected. Every call into ML code below
    is marked `# ⚠️ VERIFY(§13.2 <item>)` against the master plan's handoff checklist.

    This adapter deliberately FAILS LOUDLY with a typed, actionable error rather than
    guessing. It will never return a fabricated result. `healthcheck()` decides at runtime
    whether the model is offered to users at all.

    To unblock: mount the ML repo at $ML_REPO_PATH, answer §13.2 items A1-A9/B1-B12/D1-D6,
    then confirm or correct the marked lines and run:
        python -m scripts.smoke_adapter --model sen2sr --input <patch.tif> --out ./out

All knowledge of the ML repo's layout, import quirks, tensor shapes and normalisation
lives in THIS FILE and nowhere else.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio

from app.ml.adapters.base import BaseAdapter, tiled_apply, write_georeferenced
from app.ml.contracts import InferenceRequest, InferenceResult
from app.ml.errors import MLEntrypointNotFound

ML_ROOT = Path(os.getenv("ML_REPO_PATH", "/ml"))
MODEL_DIR = ML_ROOT / "experiments" / "models" / "sen2sr"
WEIGHTS = MODEL_DIR / "model.safetensor"
HARD_CONSTRAINT = MODEL_DIR / "hard_constraint.safetensor"
CONFIG = MODEL_DIR / "config.json"

# Candidate entrypoints, in priority order, derived from the repo listing we were given.
# ⚠️ VERIFY(§13.2 D2): confirm which of these is the intended external entrypoint.
ENTRYPOINT_CANDIDATES: list[tuple[str, str]] = [
    ("predictor", "predict"),
    ("predictor", "run"),
    ("load", "load_model"),
    ("sen2sr_pt", "load_model"),
    ("model", "build_model"),
]


@contextlib.contextmanager
def _chdir(path: Path):
    """Research code frequently uses relative paths. Enter its directory defensively."""
    prev = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(prev)


class Sen2SRAdapter(BaseAdapter):
    key = "sen2sr"
    display_name = "SEN2SR"
    version = "0.1.0"

    # ⚠️ VERIFY(§13.2 B7/B8): scale factor and output GSD must be confirmed by MEASURING
    # a real output file, not read from config.json.
    scale_factor = 4
    input_gsd_m = 10.0
    output_gsd_m = 2.5

    # ⚠️ VERIFY(§13.2 A2/B9): band list AND ORDER. A silent swap here produces a
    # plausible-looking but scientifically wrong result with no error anywhere.
    input_bands = ["B02", "B03", "B04", "B08"]
    output_bands = ["B02", "B03", "B04", "B08"]

    requires_gpu = True
    supports_uncertainty = False   # 🚨 flip ONLY when the ML team ships it (§13.2 E2)
    supports_sampling = False      # ⚠️ VERIFY(§13.2 E3): true if latent-diffusion sampling exists
    is_baseline = False

    # ⚠️ VERIFY(§13.2 C1/C2): set from MEASURED peak VRAM, not guessed.
    max_input_px = 512
    default_tile_px = 128
    tile_overlap_px = 16

    def __init__(self, spec: Any = None):
        self._model = None
        self._device: str | None = None
        self._entrypoint: Callable | None = None
        self._spec = spec
        if spec is not None:
            for attr in ("scale_factor", "input_bands", "output_bands",
                         "max_input_px", "default_tile_px", "tile_overlap_px"):
                val = getattr(spec, attr, None)
                if val is not None:
                    setattr(self, attr, val)

    # ------------------------------------------------------------------ health
    def healthcheck(self) -> dict[str, Any]:
        """Decides at runtime whether this model is offered to users at all."""
        repo = ML_ROOT.exists()
        model_dir = MODEL_DIR.exists()
        weights = WEIGHTS.exists()
        try:
            import torch  # noqa: F401
            torch_ok, cuda = True, _cuda_available()
        except Exception:
            torch_ok, cuda = False, False

        if not repo:
            reason = "ML_REPO_NOT_MOUNTED"
        elif not model_dir:
            reason = "MODEL_DIR_NOT_FOUND"
        elif not weights:
            reason = "WEIGHTS_NOT_FOUND"
        elif not torch_ok:
            reason = "TORCH_NOT_INSTALLED"
        elif self.requires_gpu and not cuda:
            reason = "NO_CUDA_DEVICE"
        else:
            reason = None

        return {
            "available": reason is None,
            "reason": reason,
            "ml_repo_path": str(ML_ROOT),
            "ml_repo_present": repo,
            "model_dir_present": model_dir,
            "weights_present": weights,
            "hard_constraint_present": HARD_CONSTRAINT.exists(),
            "config_present": CONFIG.exists(),
            "torch_installed": torch_ok,
            "cuda_available": cuda,
            "verified_against_repo": False,  # 🚨 set true only after §13.2 sign-off
        }

    # -------------------------------------------------------------------- load
    def _ensure_importable(self) -> None:
        for p in (str(ML_ROOT), str(MODEL_DIR)):
            if p not in sys.path:
                sys.path.insert(0, p)

    def _resolve_entrypoint(self) -> tuple[str, str, Callable]:
        """Try the documented candidates; raise an actionable error listing what we tried."""
        self._ensure_importable()
        tried: list[str] = []
        for module_name, func_name in ENTRYPOINT_CANDIDATES:
            tried.append(f"{module_name}.{func_name}")
            try:
                mod = importlib.import_module(module_name)
            except Exception:
                continue
            fn = getattr(mod, func_name, None)
            if callable(fn):
                return module_name, func_name, fn
        raise MLEntrypointNotFound(
            "Could not locate a SEN2SR inference entrypoint in the mounted ML repository. "
            "Confirm the intended entrypoint with the ML team (master plan §13.2 item D2) "
            "and update ENTRYPOINT_CANDIDATES in sen2sr_adapter.py.",
            {"ml_root": str(ML_ROOT), "model_dir": str(MODEL_DIR), "tried": tried},
        )

    def load(self, device: str = "cuda") -> None:
        if self._model is not None and self._device == device:
            return
        health = self.healthcheck()
        if not health["available"]:
            raise MLEntrypointNotFound(
                f"SEN2SR is not available: {health['reason']}.", health)

        t0 = time.perf_counter()
        module_name, func_name, fn = self._resolve_entrypoint()
        with _chdir(MODEL_DIR):
            # ⚠️ VERIFY(§13.2 D2): exact signature. Tolerant call: try kwargs, fall back.
            try:
                self._model = fn(weights_path=str(WEIGHTS), config_path=str(CONFIG), device=device)
            except TypeError:
                self._model = fn(str(WEIGHTS))
        self._entrypoint = fn
        self._device = device
        with contextlib.suppress(Exception):
            self._model.eval()
        _log_load(module_name, func_name, time.perf_counter() - t0, device)

    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> None:
        self._model = None
        self._device = None
        _empty_cuda_cache()

    # ------------------------------------------------------------------- infer
    def infer(self, req: InferenceRequest) -> InferenceResult:
        t0 = time.perf_counter()
        try:
            import torch
        except Exception:
            return self._fail("TORCH_NOT_INSTALLED",
                              "PyTorch is not installed in this worker image.", t0)
        try:
            self.load(req.device)
            if req.seed is not None:
                torch.manual_seed(req.seed)
                np.random.seed(req.seed)

            with rasterio.open(req.input_path) as src:
                need = len(self.input_bands)
                if src.count < need:
                    return self._fail("BAND_COUNT_MISMATCH",
                                      f"Expected {need} bands, got {src.count}.", t0)
                arr = src.read(indexes=list(range(1, need + 1))).astype(np.float32)
                profile, crs, transform = src.profile.copy(), src.crs, src.transform
                src_dtype = src.dtypes[0]

            if max(arr.shape[1], arr.shape[2]) > self.max_input_px:
                return self._fail("INPUT_TOO_LARGE",
                                  f"Input exceeds max_input_px={self.max_input_px}.", t0)

            # ⚠️ VERIFY(§13.2 A4/A5): NORMALISATION.
            # It is deliberately NOT applied here. The master plan's strong recommendation
            # is that normalisation lives inside the model so it can never drift from the
            # weights. If the ML team confirms the model expects pre-scaled input, apply it
            # HERE and nowhere else, and record the formula in docs/ML-INTEGRATION.md.

            def _forward(patch: np.ndarray) -> np.ndarray:
                # ⚠️ VERIFY(§13.2 A9): batch dimension convention (B,C,H,W) vs (C,H,W).
                t = torch.from_numpy(patch).unsqueeze(0).to(self._device)
                with torch.inference_mode():
                    out = self._model(t)
                if isinstance(out, (tuple, list)):
                    out = out[0]
                return out.squeeze(0).float().cpu().numpy()

            sr, n_tiles = tiled_apply(
                arr, self.scale_factor, req.tile_px or self.default_tile_px,
                req.tile_overlap_px, _forward, req.progress_cb)

            # ⚠️ VERIFY(§13.2 B11): assumed the model returns a BARE ARRAY with no
            # geospatial reference. We re-attach CRS/transform here. If the model DOES
            # return a georeferenced raster, use its transform instead of deriving one.
            out_path = req.output_dir / f"{req.run_id}_sr.tif"
            gsd = write_georeferenced(
                out_path, sr, profile, transform, crs, self.scale_factor, self.output_bands,
                tags={
                    "SR_MODEL": self.key, "SR_MODEL_VERSION": self.version,
                    "ML_COMMIT": os.getenv("ML_COMMIT", "unknown"),
                    "PIXELFORGE_CONTRACT": "0.1",
                    "DISCLAIMER": ("Contains model-inferred detail not directly observed "
                                   "by the sensor. Not for cadastral, legal, navigational "
                                   "or safety-critical measurement."),
                },
                dtype=src_dtype,
            )

            return InferenceResult(
                ok=True, sr_path=out_path,
                uncertainty_path=None,       # 🚨 NEVER fabricate. None until ML delivers (§13.2 E2)
                uncertainty_method=None,
                output_gsd_m=gsd, output_bands=list(self.output_bands),
                scale_factor=float(self.scale_factor), dtype=src_dtype,
                value_range=(float(np.nanmin(sr)), float(np.nanmax(sr))),
                runtime_s=round(time.perf_counter() - t0, 3),
                peak_vram_mb=_peak_vram_mb(), device_name=_device_name(),
                tiles_processed=n_tiles,
                warnings=["Adapter not yet verified against the ML repository (ADR-004)."],
            )

        except MLEntrypointNotFound as exc:
            return self._fail(exc.code, exc.message, t0)
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if name == "OutOfMemoryError" or "CUDA out of memory" in str(exc):
                _empty_cuda_cache()
                return self._fail("GPU_OUT_OF_MEMORY", str(exc), t0)
            return self._fail("INFERENCE_FAILED", f"{name}: {exc}", t0)
        finally:
            _empty_cuda_cache()


# ---------------------------------------------------------------- torch shims
def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _peak_vram_mb() -> int | None:
    try:
        import torch
        if torch.cuda.is_available():
            return int(torch.cuda.max_memory_allocated() / 1024 / 1024)
    except Exception:
        pass
    return None


def _device_name() -> str | None:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
        return "cpu"
    except Exception:
        return None


def _empty_cuda_cache() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _log_load(module: str, func: str, secs: float, device: str) -> None:
    try:
        from app.core.logging import get_logger
        get_logger(__name__).info("sen2sr.loaded", entrypoint=f"{module}.{func}",
                                  load_s=round(secs, 2), device=device)
    except Exception:
        pass
