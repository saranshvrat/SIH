"""
Adapter for models packaged in the WEO-SAS/sen2sr HuggingFace layout.

Unlike sen2sr_adapter.py (BLOCKED — no ML repo, guessed entrypoints), this adapter
drives real, inspected code: the public `sen2sr` PyPI package (pypi.org/project/sen2sr,
https://github.com/ESAOpenSR/sen2sr), whose source was read end-to-end while building
this integration (CNNSR, MambaSR, HardConstraint, nonreference.srmodel). The CNN
variant (`sen2sr_cnn`) was load-tested and run end-to-end in a CPU sandbox against the
exact weights shipped here: zero missing/unexpected state_dict keys, correct (4,512,512)
output shape for a 128px input tile.

Two model_catalog.yaml entries share this file via the two small subclasses at the
bottom (`Sen2SRCNNAdapter`, `MambaSen2SRAdapter`). Everything model-specific — weights
file name, architecture, band count, checkpoint format — is read from that model's own
config.json, using the exact flat-directory HuggingFace layout documented in the
sen2sr_cnn README (config.json + weights + hard_constraint, one folder per model key,
under settings.SEN2SR_HF_WEIGHTS_DIR).

⚠️ TWO THINGS VERIFIED BUT WORTH RE-CHECKING IF THIS EVER MISBEHAVES:

  1. BAND ORDER. The pipeline (app/domains/pipeline.py WANTED_BANDS) always hands every
     adapter a GeoTIFF with bands in [B02, B03, B04, B08] (Blue, Green, Red, NIR) order —
     that is fixed platform-wide, not something a model's own `input_bands` declaration
     can change. The upstream WEO-SAS/sen2sr package's own README says its RGBN variants
     expect [B04, B03, B02, B08] (Red, Green, Blue, NIR) instead. This adapter permutes
     in and back out explicitly via _PIPELINE_TO_SEN2SR_ORDER below. Silently skipping
     this would swap the red and blue channels — a "plausible-looking but scientifically
     wrong" result with no error, exactly the failure class this codebase is built to
     prevent (see base.py / verify.py docstrings).

  2. NORMALISATION. Every other adapter in this codebase deliberately does NOT normalise
     (app/services/preprocessing/pipeline.py: "NORMALISATION IS DELIBERATELY ABSENT... it
     stays inside the adapter"). For THIS model family specifically, the upstream
     predictor.py DOES require [0,1]-scaled input (divides raw Sentinel-2 DN by
     `normalization_factor`, 10000, before calling the network) and returns output in
     that same normalised range. This adapter reproduces that exact behaviour (confirmed
     by reading WEO-SAS/sen2sr's predictor.py `predict_tif`), then multiplies the output
     back up by `normalization_factor` before writing, so the on-disk GeoTIFF stays in
     the same raw-DN-like physical scale as every other model's output in this catalog.

MAMBA VARIANT CAVEATS (see model_catalog.yaml entry + requirements/ml.txt for detail):
  - Requires the `mamba_ssm` package, whose selective-scan op is CUDA-only (no CPU
    fallback exists upstream) — this could not be built or run in a GPU-less sandbox,
    so this variant is verified structurally (state_dict keys match the real MambaSR
    module tree, shapes match the config) but NOT run end-to-end. healthcheck() will
    correctly report it unavailable until mamba_ssm is actually installed on a CUDA box.
  - hard_constraint.safetensor was not shipped with the fine-tuned checkpoint; see the
    "_comment_hard_constraint" note in that model's config.json for why reusing the CNN
    variant's file is a documented, size-verified substitution rather than a guess.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from app.core.config import settings
from app.ml.adapters.base import BaseAdapter, tiled_apply, write_georeferenced
from app.ml.contracts import InferenceRequest, InferenceResult

# Pipeline canonical order -> WEO-SAS/sen2sr RGBN order. Swapping only positions 0 and 2
# (B02<->B04, i.e. Blue<->Red) makes this permutation its own inverse, so the same array
# converts pipeline-order -> model-order on the way in AND model-order -> pipeline-order
# on the way out.
_PIPELINE_TO_SEN2SR_ORDER = [2, 1, 0, 3]


class Sen2SRHFAdapter(BaseAdapter):
    """
    Shared driver for any model packaged in the WEO-SAS/sen2sr flat HF layout.

    Subclasses just set `key`, `display_name`, `requires_gpu`. Everything else —
    architecture, channel counts, weights filename, normalisation factor — is read
    from `<SEN2SR_HF_WEIGHTS_DIR>/<key>/config.json` at load time.
    """

    key = "sen2sr_hf_base"
    display_name = "SEN2SR (HF package)"
    version = "1.0.0"

    scale_factor = 4
    input_gsd_m = 10.0
    output_gsd_m = 2.5
    input_bands = ["B02", "B03", "B04", "B08"]
    output_bands = ["B02", "B03", "B04", "B08"]

    requires_gpu = True
    supports_uncertainty = False
    supports_sampling = False
    is_baseline = False

    max_input_px = 512
    default_tile_px = 128
    tile_overlap_px = 16

    def __init__(self, spec: Any = None):
        self._model = None
        self._device: str | None = None
        self._cfg: dict | None = None
        self._normalization_factor = 10000.0
        self._spec = spec
        if spec is not None:
            for attr in (
                "scale_factor",
                "input_bands",
                "output_bands",
                "max_input_px",
                "default_tile_px",
                "tile_overlap_px",
            ):
                val = getattr(spec, attr, None)
                if val is not None:
                    setattr(self, attr, val)

    # ------------------------------------------------------------------ paths
    def _weights_dir(self) -> Path:
        return Path(settings.SEN2SR_HF_WEIGHTS_DIR) / self.key

    def _read_config(self) -> dict | None:
        path = self._weights_dir() / "config.json"
        if not path.exists():
            return None
        import json

        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    # ------------------------------------------------------------------ health
    def healthcheck(self) -> dict[str, Any]:
        d = self._weights_dir()
        cfg = self._read_config()
        arch = (cfg or {}).get("architecture")

        weights_present = False
        if cfg:
            weights_present = (d / cfg["weights_file"]).exists()
        hc_present = (
            bool(cfg)
            and (
                d / cfg.get("hard_constraint_file", "hard_constraint.safetensor")
            ).exists()
        )

        try:
            import torch  # noqa: F401

            torch_ok, cuda = True, bool(torch.cuda.is_available())
        except Exception:
            torch_ok, cuda = False, False

        try:
            import sen2sr  # noqa: F401

            sen2sr_ok = True
        except Exception:
            sen2sr_ok = False

        mamba_ssm_ok: bool | None = None
        if arch == "mamba":
            try:
                import mamba_ssm  # noqa: F401

                mamba_ssm_ok = True
            except Exception:
                mamba_ssm_ok = False

        if cfg is None:
            reason = "CONFIG_NOT_FOUND"
        elif not weights_present:
            reason = "WEIGHTS_NOT_FOUND"
        elif not hc_present:
            reason = "HARD_CONSTRAINT_NOT_FOUND"
        elif not torch_ok:
            reason = "TORCH_NOT_INSTALLED"
        elif not sen2sr_ok:
            reason = "SEN2SR_PACKAGE_NOT_INSTALLED"
        elif arch == "mamba" and not mamba_ssm_ok:
            reason = "MAMBA_SSM_NOT_INSTALLED"
        elif self.requires_gpu and not cuda:
            reason = "NO_CUDA_DEVICE"
        else:
            reason = None

        return {
            "available": reason is None,
            "reason": reason,
            "weights_dir": str(d),
            "config_present": cfg is not None,
            "weights_present": weights_present,
            "hard_constraint_present": hc_present,
            "torch_installed": torch_ok,
            "cuda_available": cuda,
            "sen2sr_installed": sen2sr_ok,
            "mamba_ssm_installed": mamba_ssm_ok,
            "architecture": arch,
            # sen2sr_cnn: verified by real end-to-end inference in a CPU sandbox.
            # mamba_sen2sr_finetuned: verified structurally (keys/shapes match), not
            # run end-to-end (needs a CUDA box with mamba_ssm built — see module docstring).
            "verified_against_repo": arch == "cnn",
        }

    # -------------------------------------------------------------------- load
    def load(self, device: str = "cuda") -> None:
        if self._model is not None and self._device == device:
            return
        health = self.healthcheck()
        if not health["available"]:
            from app.ml.errors import ModelUnavailableError

            raise ModelUnavailableError(
                f"{self.display_name} is not available: {health['reason']}.", health
            )

        import safetensors.torch
        from sen2sr.models.tricks import HardConstraint
        from sen2sr.nonreference import srmodel

        cfg = self._read_config()
        d = self._weights_dir()
        arch = cfg["architecture"]

        backbone = self._build_backbone(arch, cfg)
        state_dict = self._load_backbone_state_dict(d / cfg["weights_file"], cfg)
        missing, unexpected = backbone.load_state_dict(state_dict, strict=False)
        if missing or unexpected:
            raise RuntimeError(
                f"{self.display_name}: checkpoint does not match the '{arch}' architecture "
                f"built from its own config.json — {len(missing)} missing key(s), "
                f"{len(unexpected)} unexpected key(s). First few missing: {missing[:5]}; "
                f"first few unexpected: {unexpected[:5]}."
            )
        backbone.eval()
        for p in backbone.parameters():
            p.requires_grad = False
        backbone.to(device)

        hc_weights = safetensors.torch.load_file(
            d / cfg.get("hard_constraint_file", "hard_constraint.safetensor")
        )
        hard_constraint = HardConstraint(
            low_pass_mask=hc_weights["weights"].to(device), device=device
        )

        self._model = srmodel(backbone, hard_constraint, device)
        self._device = device
        self._normalization_factor = float(cfg.get("normalization_factor", 10000))
        # HardConstraint's low_pass_mask (loaded above) is a fixed-size Fourier-domain
        # tensor sized for exactly one HR output shape (patch_size * scale_factor).
        # Unlike a plain conv backbone, this is NOT resolution-flexible: feeding it a
        # differently-sized tile raises a tensor-shape RuntimeError deep inside
        # HardConstraint.forward(). So — unlike bicubic/wsx4 — tile_px for this model
        # family is fixed by its own config, not caller-adjustable.
        self._fixed_tile_px = int(cfg.get("patch_size", self.default_tile_px))
        _log_load(self.key, arch, device)

    def _build_backbone(self, arch: str, cfg: dict):
        if arch == "cnn":
            from sen2sr.models.opensr_baseline.cnn import CNNSR

            return CNNSR(
                cfg["in_channels"],
                cfg["out_channels"],
                cfg["feature_channels"],
                cfg.get("upscale", cfg["scaling_factor"]),
                cfg["bias"],
                cfg["train_mode"],
                cfg["num_blocks"],
            )
        if arch == "mamba":
            from sen2sr.models.opensr_baseline.mamba import MambaSR

            return MambaSR(
                img_size=tuple(cfg["img_size"]),
                in_channels=cfg["in_channels"],
                out_channels=cfg["out_channels"],
                embed_dim=cfg["embed_dim"],
                depths=cfg["depths"],
                num_heads=cfg["num_heads"],
                mlp_ratio=cfg["mlp_ratio"],
                upscale=cfg.get("upscale", cfg["scaling_factor"]),
                attention_type=cfg["attention_type"],
                upsampler=cfg["upsampler"],
                resi_connection=cfg["resi_connection"],
                operation_attention=cfg["operation_attention"],
            )
        raise ValueError(f"Unsupported architecture '{arch}' for {self.key}")

    def _load_backbone_state_dict(self, weights_path: Path, cfg: dict) -> dict:
        import torch
        import safetensors.torch

        if weights_path.suffix in (".safetensor", ".safetensors"):
            return safetensors.torch.load_file(weights_path)

        # Anything else (e.g. the fine-tuned mamba .pth) is treated as a raw PyTorch
        # checkpoint that may carry epoch/optimizer/loss alongside the weights, per
        # config.json's "checkpoint_state_dict_key" (see that file's own comment).
        ckpt = torch.load(weights_path, map_location="cpu")
        sd_key = cfg.get("checkpoint_state_dict_key", "model_state_dict")
        if isinstance(ckpt, dict) and sd_key in ckpt:
            return ckpt[sd_key]
        if (
            isinstance(ckpt, dict)
            and ckpt
            and all(torch.is_tensor(v) for v in ckpt.values())
        ):
            return ckpt  # already a bare state_dict
        raise RuntimeError(
            f"Could not find a state_dict under key '{sd_key}' in checkpoint "
            f"{weights_path.name}; got top-level keys {list(ckpt)[:10] if isinstance(ckpt, dict) else type(ckpt)}."
        )

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

            self.load(req.device)
            if req.seed is not None:
                torch.manual_seed(req.seed)
                np.random.seed(req.seed)

            with rasterio.open(req.input_path) as src:
                need = len(self.input_bands)
                if src.count < need:
                    return self._fail(
                        "BAND_COUNT_MISMATCH",
                        f"Expected {need} bands, got {src.count}.",
                        t0,
                    )
                arr = src.read(indexes=list(range(1, need + 1))).astype(np.float32)
                profile, crs, transform = src.profile.copy(), src.crs, src.transform
                src_dtype = src.dtypes[0]

            if max(arr.shape[1], arr.shape[2]) > self.max_input_px:
                return self._fail(
                    "INPUT_TOO_LARGE",
                    f"Input exceeds max_input_px={self.max_input_px}.",
                    t0,
                )

                # Pipeline order [B02,B03,B04,B08] -> sen2sr's expected [B04,B03,B02,B08].
            arr = arr[_PIPELINE_TO_SEN2SR_ORDER]

            # The hard-constraint mask is fixed-size (patch_size*scale_factor), so an
            # input smaller than patch_size must be padded up before inference and
            # cropped back down after, or it crashes with a tensor-size mismatch.
            orig_h, orig_w = arr.shape[1], arr.shape[2]
            pad_h = max(0, self._fixed_tile_px - orig_h)
            pad_w = max(0, self._fixed_tile_px - orig_w)
            if pad_h or pad_w:
                arr = np.pad(arr, ((0, 0), (0, pad_h), (0, pad_w)), mode="edge")

            # Raw Sentinel-2 DN -> [0,1], exactly as WEO-SAS/sen2sr's own predictor.py
            # does in predict_tif() before calling the network.
            nf = self._normalization_factor
            x = np.clip(arr / nf, 0.0, 1.0)

            def _forward(patch: np.ndarray) -> np.ndarray:
                t = torch.from_numpy(patch).unsqueeze(0).to(self._device)
                with torch.inference_mode():
                    out = self._model(t)
                out = torch.clamp(out, min=0.0)
                return out.squeeze(0).float().cpu().numpy()

            # See the note in load(): the hard-constraint mask fixes tile_px for this
            # model family, regardless of what the caller requested. Surface that as a
            # warning rather than either silently ignoring it or crashing.
            run_warnings: list[str] = []
            effective_tile_px = self._fixed_tile_px
            if req.tile_px and req.tile_px != effective_tile_px:
                run_warnings.append(
                    f"{self.display_name} uses a fixed {effective_tile_px}px input tile "
                    f"(its hard-constraint module is baked for exactly that size); the "
                    f"requested tile_px={req.tile_px} was ignored."
                )

            sr_norm, n_tiles = tiled_apply(
                x,
                self.scale_factor,
                effective_tile_px,
                req.tile_overlap_px,
                _forward,
                req.progress_cb,
            )

            # Back to raw-DN physical scale, then back to the pipeline's canonical
            # [B02,B03,B04,B08] band order (permutation is self-inverse, see top of file).
            sr = (sr_norm * nf)[_PIPELINE_TO_SEN2SR_ORDER]
            if pad_h or pad_w:
                sr = sr[:, : orig_h * self.scale_factor, : orig_w * self.scale_factor]

            out_path = req.output_dir / f"{req.run_id}_sr.tif"
            gsd = write_georeferenced(
                out_path,
                sr,
                profile,
                transform,
                crs,
                self.scale_factor,
                self.output_bands,
                tags={
                    "SR_MODEL": self.key,
                    "SR_MODEL_VERSION": self.version,
                    "PIXELFORGE_CONTRACT": "0.1",
                    "DISCLAIMER": (
                        "Contains model-inferred detail not directly observed "
                        "by the sensor. Not for cadastral, legal, navigational "
                        "or safety-critical measurement."
                    ),
                },
                dtype=src_dtype,
            )

            return InferenceResult(
                ok=True,
                sr_path=out_path,
                uncertainty_path=None,
                uncertainty_method=None,
                output_gsd_m=gsd,
                output_bands=list(self.output_bands),
                scale_factor=float(self.scale_factor),
                dtype=src_dtype,
                value_range=(float(np.nanmin(sr)), float(np.nanmax(sr))),
                runtime_s=round(time.perf_counter() - t0, 3),
                peak_vram_mb=_peak_vram_mb(),
                device_name=_device_name(),
                tiles_processed=n_tiles,
                warnings=run_warnings,
            )
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if name == "OutOfMemoryError" or "CUDA out of memory" in str(exc):
                _empty_cuda_cache()
                return self._fail("GPU_OUT_OF_MEMORY", str(exc), t0)
            return self._fail("INFERENCE_FAILED", f"{name}: {exc}", t0)
        finally:
            _empty_cuda_cache()


# ---------------------------------------------------------------- torch shims
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


def _log_load(key: str, arch: str, device: str) -> None:
    try:
        from app.core.logging import get_logger

        get_logger(__name__).info(
            "sen2sr_hf.loaded", key=key, architecture=arch, device=device
        )
    except Exception:
        pass


# ============================================================================
# Per-model catalogue entries. Both read everything else from their own
# config.json under ml_weights_sen2sr/<key>/ — see module docstring.
# ============================================================================


class Sen2SRCNNAdapter(Sen2SRHFAdapter):
    """SEN2SRLite RGBN x4 (CNN / SPAN backbone). Verified end-to-end in a CPU sandbox."""

    key = "sen2sr_cnn"
    display_name = "SEN2SR-CNN (SEN2SRLite RGBN x4)"
    version = "1.0.0"

    # Plain Conv2d backbone — no custom CUDA ops. Runs on CPU (slower) or GPU.
    # Set True (or pass device="cuda" via job config) if you want it to always
    # use the GPU worker's card; left False here since it was verified on CPU.
    requires_gpu = False

    default_tile_px = 128
    tile_overlap_px = 16
    max_input_px = 512


class MambaSen2SRAdapter(Sen2SRHFAdapter):
    """
    SEN2SR RGBN x4 (Mamba backbone), fine-tuned checkpoint (epoch 3).

    requires_gpu=True is load-bearing, not just documentation: mamba_ssm's
    selective_scan_fn has no CPU implementation upstream, so this model cannot run
    without CUDA regardless of what device is requested.
    """

    key = "mamba_sen2sr_finetuned"
    display_name = "SEN2SR-Mamba (fine-tuned, epoch 3)"
    version = "1.0.0"

    requires_gpu = True
    default_tile_px = 128
    tile_overlap_px = 16
    max_input_px = 512
