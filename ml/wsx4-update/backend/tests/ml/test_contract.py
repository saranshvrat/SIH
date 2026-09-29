"""Phase 3 acceptance tests — master plan §11 Phase 3 + §16.3."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio

from app.ml.errors import ModelUnavailableError, OutputContractViolation
from app.ml.registry import available_model_keys, get_adapter, list_models, run_model
from app.ml.verify import PS_MAX_GSD_M, spectral_consistency_check, verify_output_contract


# --- catalogue honesty -----------------------------------------------------
def test_catalogue_never_upgrades_declared_status():
    """Runtime health may only DOWNGRADE a model, never mark an absent model available."""
    for m in list_models():
        if m["status"] == "available":
            assert m["health"]["available"] is True


def test_unimplemented_models_are_unavailable_with_a_reason():
    by_key = {m["key"]: m for m in list_models()}
    for key in ("mamba_sr", "ldsr_s2", "satlas"):
        assert by_key[key]["status"] == "unavailable"
        assert by_key[key]["unavailable_reason"]


def test_mamba_sr_is_never_runnable():
    """PPT claims it; the repository has zero evidence. It must not be instantiable."""
    with pytest.raises(ModelUnavailableError):
        get_adapter("mamba_sr")


def test_bicubic_is_always_available():
    assert "bicubic" in available_model_keys()


# --- the geospatial contract ----------------------------------------------
@pytest.mark.parametrize("model", ["bicubic"])
def test_preserves_geospatial_reference(model, scene_tif, tmp_path):
    result, integrity = run_model(model, scene_tif, tmp_path / "sr.tif", {"run_id": "t1"})
    assert result.ok, result.error_message
    with rasterio.open(scene_tif) as src, rasterio.open(result.sr_path) as dst:
        assert dst.crs == src.crs
        assert dst.count == src.count
        assert abs(src.res[0] / dst.res[0] - 4.0) < 0.01
        assert dst.res[0] < PS_MAX_GSD_M          # the literal PS 26142 requirement
    assert integrity["meets_ps_requirement"] is True
    assert integrity["bounds_max_offset_m"] <= dst.res[0]


def test_band_order_is_preserved(band_id_tif, tmp_path):
    """Each input band is a distinct constant; a silent reorder changes the means."""
    result, _ = run_model("bicubic", band_id_tif, tmp_path / "sr.tif", {"run_id": "t2"})
    assert result.ok
    with rasterio.open(result.sr_path) as dst:
        means = [float(dst.read(i).mean()) for i in range(1, dst.count + 1)]
    assert means == sorted(means), f"band order changed during inference: {means}"
    for got, want in zip(means, (1000, 2000, 3000, 4000)):
        assert abs(got - want) < 40


def test_band_descriptions_are_written(scene_tif, tmp_path):
    result, _ = run_model("bicubic", scene_tif, tmp_path / "sr.tif", {"run_id": "t3"})
    with rasterio.open(result.sr_path) as dst:
        assert list(dst.descriptions) == ["B02", "B03", "B04", "B08"]


def test_provenance_tags_travel_with_the_file(scene_tif, tmp_path):
    """Provenance must live in the GeoTIFF itself, not only in our database."""
    result, _ = run_model("bicubic", scene_tif, tmp_path / "sr.tif", {"run_id": "t4"})
    with rasterio.open(result.sr_path) as dst:
        tags = dst.tags()
    assert tags["SR_MODEL"] == "bicubic"
    assert tags["PIXELFORGE_CONTRACT"] == "0.1"
    assert "DISCLAIMER" in tags


def test_reproducible_with_seed(scene_tif, tmp_path):
    a, _ = run_model("bicubic", scene_tif, tmp_path / "a.tif", {"run_id": "a", "seed": 42})
    b, _ = run_model("bicubic", scene_tif, tmp_path / "b.tif", {"run_id": "b", "seed": 42})
    with rasterio.open(a.sr_path) as fa, rasterio.open(b.sr_path) as fb:
        assert np.array_equal(fa.read(), fb.read())


def test_spectral_consistency_holds_for_a_faithful_upsampler(scene_tif, tmp_path):
    result, _ = run_model("bicubic", scene_tif, tmp_path / "sr.tif", {"run_id": "t5"})
    check = spectral_consistency_check(scene_tif, result.sr_path)
    assert check["passed"] is True
    assert check["worst_band_delta"] < 0.02


# --- tiling ----------------------------------------------------------------
def test_tiling_matches_untiled_output(scene_tif, tmp_path):
    """
    The real seam test: a heavily-tiled run must reproduce a single-tile run.

    Measuring raw gradients would fail on genuine image edges (roads, field
    boundaries), so we compare tiled vs untiled directly instead.
    """
    tiled, _ = run_model("bicubic", scene_tif, tmp_path / "a.tif",
                         {"run_id": "tiled", "tile_px": 64, "tile_overlap_px": 16})
    whole, _ = run_model("bicubic", scene_tif, tmp_path / "b.tif",
                         {"run_id": "whole", "tile_px": 4096, "tile_overlap_px": 16})
    assert tiled.tiles_processed > 4 and whole.tiles_processed == 1

    with rasterio.open(tiled.sr_path) as a, rasterio.open(whole.sr_path) as b:
        da, db = a.read().astype(np.float64), b.read().astype(np.float64)
    scale = max(float(db.max()), 1.0)
    assert float(np.abs(da - db).mean()) / scale < 0.01, "cosine blending left visible seams"


def test_progress_callback_is_invoked_monotonically(scene_tif, tmp_path):
    seen = []
    run_model("bicubic", scene_tif, tmp_path / "sr.tif",
              {"run_id": "t7", "tile_px": 64, "progress_cb": lambda p, d: seen.append(p)})
    assert seen and seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


# --- failure paths ---------------------------------------------------------
def test_missing_input_fails_cleanly(tmp_path):
    result, _ = run_model("bicubic", tmp_path / "nope.tif", tmp_path / "sr.tif", {"run_id": "x"})
    assert result.ok is False and result.error_code == "INFERENCE_FAILED"


def test_too_few_bands_is_a_typed_failure(invalid_factory, tmp_path):
    result, _ = run_model("bicubic", invalid_factory("too_few_bands"),
                          tmp_path / "sr.tif", {"run_id": "x"})
    assert result.ok is False and result.error_code == "BAND_COUNT_MISMATCH"


def test_oversized_input_is_rejected_before_inference(tmp_path):
    from tests.fixtures.make_fixtures import make_scene
    big = make_scene(tmp_path / "big.tif", size=2500)
    result, _ = run_model("bicubic", big, tmp_path / "sr.tif", {"run_id": "x"})
    assert result.ok is False and result.error_code == "INPUT_TOO_LARGE"


def test_contract_violation_is_raised_not_swallowed(scene_tif, tmp_path):
    """A model that lies about its scale factor must be caught, not served."""
    adapter = get_adapter("bicubic")
    result, _ = run_model("bicubic", scene_tif, tmp_path / "sr.tif", {"run_id": "v"})

    class Lying:
        output_bands = adapter.output_bands
        scale_factor = 8            # claims x8, actually produced x4
    with pytest.raises(OutputContractViolation) as exc:
        verify_output_contract(result, Lying(), scene_tif)
    assert exc.value.details["check"] == "SCALE_FACTOR_MISMATCH"


def test_sen2sr_is_blocked_but_never_fabricates(tmp_path, scene_tif):
    """With no ML repo mounted, SEN2SR must refuse — not return a made-up result."""
    from app.ml.adapters.sen2sr_adapter import Sen2SRAdapter
    health = Sen2SRAdapter().healthcheck()
    assert health["available"] is False
    assert health["reason"] in {"ML_REPO_NOT_MOUNTED", "MODEL_DIR_NOT_FOUND",
                                "WEIGHTS_NOT_FOUND", "TORCH_NOT_INSTALLED", "NO_CUDA_DEVICE"}
    assert health["verified_against_repo"] is False

    with pytest.raises(ModelUnavailableError) as exc:
        run_model("sen2sr", scene_tif, tmp_path / "sr.tif", {"run_id": "s"})
    assert exc.value.details["reason"] == health["reason"]
    assert not (tmp_path / "sr.tif").exists(), "a blocked model must not write any output"


# --- WSX4-SPATRAD (real ONNX model) ----------------------------------------
# These only run if the weight files are actually present (they're not
# committed to the repo — see docs/ML-INTEGRATION.md). This lets the suite
# stay green for anyone who hasn't placed the weights, while still real-testing
# the integration for anyone who has.
import os as _os
from pathlib import Path as _Path

_ONNX_DIR = _Path(_os.environ.get("ONNX_WEIGHTS_DIR", "./ml_weights"))
_WSX4_AVAILABLE = (_ONNX_DIR / "wsx4_spatrad.onnx").exists()


@pytest.mark.skipif(not _WSX4_AVAILABLE, reason="wsx4_spatrad.onnx not present in ONNX_WEIGHTS_DIR")
def test_wsx4_spatrad_meets_geospatial_contract(scene_tif, tmp_path):
    """Real ONNX inference: CRS/bounds preserved, PS <4m requirement met."""
    result, integrity = run_model("wsx4_spatrad", scene_tif, tmp_path / "sr.tif", {"run_id": "wsx4"})
    assert result.ok, result.error_message
    assert integrity["crs_preserved"] is True
    assert integrity["bounds_max_offset_m"] == 0.0
    assert integrity["meets_ps_requirement"] is True
    with rasterio.open(result.sr_path) as dst:
        assert dst.count == 4
        assert list(dst.descriptions) == ["B02", "B03", "B04", "B08"]
        assert dst.tags()["SR_MODEL"] == "wsx4_spatrad"


@pytest.mark.skipif(not _WSX4_AVAILABLE, reason="wsx4_spatrad.onnx not present in ONNX_WEIGHTS_DIR")
def test_wsx4_spatrad_output_not_flat_or_garbage(scene_tif, tmp_path):
    """
    Guards against silently feeding wrongly-scaled input.

    The model's own documented failure mode is: normalise the input (divide by
    10000) and it produces flat/saturated garbage with NO error. This test
    would catch a regression where someone "fixes" the adapter to add
    normalisation, thinking raw DN input looks like a bug.
    """
    result, _ = run_model("wsx4_spatrad", scene_tif, tmp_path / "sr.tif", {"run_id": "wsx4_sanity"})
    assert result.ok
    with rasterio.open(result.sr_path) as dst:
        band = dst.read(1).astype("float64")
    assert band.std() > 1.0, "output is suspiciously flat — check for accidental input normalisation"


@pytest.mark.skipif(not _WSX4_AVAILABLE, reason="wsx4_spatrad_mumbai_ft.onnx not present")
def test_wsx4_spatrad_mumbai_variant_also_works(scene_tif, tmp_path):
    result, integrity = run_model("wsx4_spatrad_mumbai_ft", scene_tif, tmp_path / "sr.tif", {"run_id": "wsx4_mumbai"})
    assert result.ok, result.error_message
    assert integrity["meets_ps_requirement"] is True
