"""
Model registry — resolves `model_key` to a live adapter and enforces the output contract.

This is the only module the rest of the backend imports to run a model.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.ml.contracts import InferenceRequest, InferenceResult, SRModelAdapter
from app.ml.errors import ModelUnavailableError, UnknownModelError
from app.ml.verify import verify_output_contract

CATALOG_PATH = Path(__file__).parent / "model_catalog.yaml"

_lock = threading.Lock()
_loaded: dict[str, SRModelAdapter] = {}


@dataclass
class ModelSpec:
    key: str
    display_name: str
    adapter: str | None
    status: str
    description: str = ""
    reason: str | None = None
    is_baseline: bool = False
    scale_factor: int = 4
    input_gsd_m: float = 10.0
    output_gsd_m: float = 2.5
    input_bands: list[str] = field(default_factory=lambda: ["B02", "B03", "B04", "B08"])
    output_bands: list[str] = field(
        default_factory=lambda: ["B02", "B03", "B04", "B08"]
    )
    requires_gpu: bool = False
    supports_uncertainty: bool = False
    supports_sampling: bool = False
    default_tile_px: int = 128
    tile_overlap_px: int = 16
    max_input_px: int = 512
    best_for: list[str] = field(default_factory=list)
    citation_url: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    # ONNX weight-file adapters (e.g. WSX4-SPATRAD): the .onnx file lives under
    # ONNX_WEIGHTS_DIR, and `margin_px` is OUTPUT-resolution context-padding
    # pixels cropped off each side after inference (see ADR-007).
    weights_filename: str | None = None
    margin_px: int = 0


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, ModelSpec]:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    out: dict[str, ModelSpec] = {}
    valid = set(ModelSpec.__dataclass_fields__)
    for entry in raw.get("models", []):
        out[entry["key"]] = ModelSpec(**{k: v for k, v in entry.items() if k in valid})
    return out


def get_spec(key: str) -> ModelSpec:
    try:
        return load_catalog()[key]
    except KeyError as exc:
        raise UnknownModelError(f"Unknown model key: {key!r}") from exc


def get_adapter(key: str) -> SRModelAdapter:
    """Return a (cached) adapter instance. GPU worker is --pool=solo so this is safe."""
    with _lock:
        if key in _loaded:
            return _loaded[key]
        spec = get_spec(key)
        if spec.status == "unavailable" or not spec.adapter:
            raise ModelUnavailableError(
                f"Model {spec.display_name} is not available.",
                {"key": key, "reason": spec.reason or "NOT_INTEGRATED"},
            )
        module_path, _, cls_name = spec.adapter.partition(":")
        cls = getattr(importlib.import_module(module_path), cls_name)
        adapter = cls(spec)

        # Health gate: a declared-available model whose weights/repo/GPU are missing is
        # NOT handed out. This is what stops an unavailable model reaching a user.
        health = adapter.healthcheck()
        if not health.get("available", False):
            raise ModelUnavailableError(
                f"Model {spec.display_name} is not currently available.",
                {
                    "key": key,
                    "reason": health.get("reason", "UNHEALTHY"),
                    "health": health,
                },
            )

        _loaded[key] = adapter
        return adapter


def describe(key: str) -> dict[str, Any]:
    """Catalogue entry + LIVE health, as served by GET /api/v1/models."""
    spec = get_spec(key)
    health: dict[str, Any] = {
        "available": False,
        "reason": spec.reason or "NOT_INTEGRATED",
    }
    if spec.adapter:
        try:
            health = get_adapter(key).healthcheck()
        except ModelUnavailableError as exc:
            health = {
                "available": False,
                "reason": exc.details.get("reason", "MODEL_UNAVAILABLE"),
            }
        except Exception as exc:  # noqa: BLE001
            health = {
                "available": False,
                "reason": "ADAPTER_LOAD_FAILED",
                "error": str(exc),
            }

    # Declared status can only be DOWNGRADED by health, never upgraded.
    effective = (
        "available"
        if (spec.status == "available" and health.get("available"))
        else "unavailable"
    )
    return {
        "key": spec.key,
        "display_name": spec.display_name,
        "description": spec.description.strip(),
        "status": effective,
        "unavailable_reason": None
        if effective == "available"
        else health.get("reason", spec.reason),
        "is_baseline": spec.is_baseline,
        "scale_factor": spec.scale_factor,
        "input_gsd_m": spec.input_gsd_m,
        "output_gsd_m": spec.output_gsd_m,
        "input_bands": spec.input_bands,
        "output_bands": spec.output_bands,
        "requires_gpu": spec.requires_gpu,
        "supports_uncertainty": spec.supports_uncertainty,
        "supports_sampling": spec.supports_sampling,
        "max_input_px": spec.max_input_px,
        "tile_px": spec.default_tile_px,
        "tile_overlap_px": spec.tile_overlap_px,
        "best_for": spec.best_for,
        "citation_url": spec.citation_url,
        "verified_against_repo": bool(
            spec.verification.get("verified_against_repo", spec.is_baseline)
        ),
        "health": health,
    }


def list_models() -> list[dict[str, Any]]:
    return [describe(k) for k in load_catalog()]


def available_model_keys() -> list[str]:
    return [m["key"] for m in list_models() if m["status"] == "available"]


def run_model(
    model_name: str, input_path: Path, output_path: Path, config: dict | None = None
) -> tuple[InferenceResult, dict]:
    """
    Convenience façade (master plan §6.3).

    Returns (result, integrity_report). Raises OutputContractViolation if the adapter
    produced something that must not be served.
    """
    config = dict(config or {})
    adapter = get_adapter(model_name)
    device = config.pop("device", "cpu" if not adapter.requires_gpu else "cuda")
    if not adapter.is_loaded():
        adapter.load(device=device)

    req = InferenceRequest(
        input_path=Path(input_path),
        output_dir=Path(output_path).parent,
        run_id=config.pop("run_id", "adhoc"),
        input_bands=list(adapter.input_bands),
        tile_px=config.pop("tile_px", None),
        tile_overlap_px=config.pop("tile_overlap_px", adapter.tile_overlap_px),
        device=device,
        seed=config.pop("seed", 42),
        num_samples=config.pop("num_samples", 1),
        want_uncertainty=config.pop("want_uncertainty", False),
        progress_cb=config.pop("progress_cb", None),
        extra=config,
    )
    result = adapter.infer(req)
    if not result.ok:
        return result, {}
    integrity = verify_output_contract(result, adapter, Path(input_path))
    return result, integrity
