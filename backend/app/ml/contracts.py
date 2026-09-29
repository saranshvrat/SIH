"""
PixelForge ML contract — FROZEN at v0.1 (master plan §6.3).

Nothing above this module knows PyTorch. Nothing below it knows HTTP.

Changing any field here is a BREAKING CHANGE: bump CONTRACT_VERSION, announce it in
the team channel, and update docs/ML-INTEGRATION.md before merging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

CONTRACT_VERSION = "0.1"

ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class InferenceRequest:
    """Everything an adapter needs. Immutable so a task cannot mutate it mid-run."""

    # --- input ---
    input_path: Path
    output_dir: Path
    run_id: str
    input_bands: list[str]

    # --- execution knobs the adapter must honour ---
    tile_px: int | None = None
    tile_overlap_px: int = 16
    device: Literal["cuda", "cpu"] = "cuda"
    seed: int | None = 42
    num_samples: int = 1
    want_uncertainty: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    # --- callbacks (excluded from equality/hash by being a plain field) ---
    progress_cb: ProgressCallback | None = None


@dataclass
class InferenceResult:
    """What an adapter returns. `None` means NOT PRODUCED — never fabricate a value."""

    ok: bool
    sr_path: Path | None

    uncertainty_path: Path | None = None
    uncertainty_method: str | None = None
    sample_paths: list[Path] = field(default_factory=list)

    # --- self-reported; independently re-verified by verify_output_contract() ---
    output_gsd_m: float | None = None
    output_bands: list[str] = field(default_factory=list)
    scale_factor: float | None = None
    dtype: str | None = None
    value_range: tuple[float, float] | None = None

    # --- telemetry ---
    runtime_s: float = 0.0
    peak_vram_mb: int | None = None
    device_name: str | None = None
    tiles_processed: int = 0

    # --- diagnostics ---
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sr_path": str(self.sr_path) if self.sr_path else None,
            "uncertainty_path": str(self.uncertainty_path) if self.uncertainty_path else None,
            "uncertainty_method": self.uncertainty_method,
            "output_gsd_m": self.output_gsd_m,
            "output_bands": list(self.output_bands),
            "scale_factor": self.scale_factor,
            "dtype": self.dtype,
            "value_range": list(self.value_range) if self.value_range else None,
            "runtime_s": self.runtime_s,
            "peak_vram_mb": self.peak_vram_mb,
            "device_name": self.device_name,
            "tiles_processed": self.tiles_processed,
            "warnings": list(self.warnings),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@runtime_checkable
class SRModelAdapter(Protocol):
    """Every runnable model implements exactly this surface. Nothing more."""

    key: str
    display_name: str
    version: str
    scale_factor: int
    input_bands: list[str]
    output_bands: list[str]
    input_gsd_m: float
    output_gsd_m: float
    requires_gpu: bool
    supports_uncertainty: bool
    supports_sampling: bool
    is_baseline: bool
    max_input_px: int
    default_tile_px: int
    tile_overlap_px: int

    def load(self, device: str = "cuda") -> None: ...
    def is_loaded(self) -> bool: ...
    def infer(self, req: InferenceRequest) -> InferenceResult: ...
    def unload(self) -> None: ...
    def healthcheck(self) -> dict[str, Any]: ...
