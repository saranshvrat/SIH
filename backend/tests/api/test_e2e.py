"""
End-to-end: upload -> job -> inference -> results -> download.

This is the master plan's Phase 14 MVP gate, exercised through the real HTTP API with
a real GeoTIFF and a real (bicubic) model. Nothing here is mocked.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.fixtures.make_fixtures import make_invalid, make_scene


def _upload(client, path: Path, **data):
    with open(path, "rb") as fh:
        return client.post("/api/v1/scenes/upload",
                           files={"file": (path.name, fh, "image/tiff")}, data=data)


# --- system ----------------------------------------------------------------
def test_health_reports_real_state(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True
    assert "bicubic" in body["models_available"]
    assert body["contract_version"] == "0.1"


def test_models_endpoint_is_honest(client):
    body = client.get("/api/v1/models").json()
    by_key = {m["key"]: m for m in body["models"]}
    assert by_key["bicubic"]["status"] == "available"
    assert by_key["mamba_sr"]["status"] == "unavailable"
    assert by_key["mamba_sr"]["unavailable_reason"]
    assert by_key["sen2sr"]["verified_against_repo"] is False


# --- upload validation -----------------------------------------------------
def test_upload_accepts_valid_sentinel2_like_raster(client, tmp_path):
    scene = make_scene(tmp_path / "ok.tif", size=128)
    r = _upload(client, scene)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["crs"] == "EPSG:32645"
    assert body["gsd_m"] == 10.0
    assert body["band_mapping_confirmed"] is True
    assert body["status"] == "ready"


@pytest.mark.parametrize("kind,code", [
    ("no_crs", "NOT_GEOREFERENCED"),
    ("wrong_gsd", "INVALID_RASTER_GSD"),
    ("too_few_bands", "INSUFFICIENT_BANDS"),
    ("not_a_raster", "CORRUPT_RASTER"),
])
def test_upload_rejects_bad_input_with_typed_codes(client, tmp_path, kind, code):
    bad = make_invalid(kind, tmp_path / f"{kind}.tif")
    r = _upload(client, bad)
    assert r.status_code in (413, 422)
    assert r.json()["error"]["code"] == code
    assert r.json()["error"]["message"]          # human-readable, not a stack trace
    assert "Traceback" not in r.text


def test_upload_dedupes_by_checksum(client, tmp_path):
    scene = make_scene(tmp_path / "dupe.tif", size=96)
    a = _upload(client, scene).json()
    b = _upload(client, scene).json()
    assert a["scene_id"] == b["scene_id"]


# --- AOI -------------------------------------------------------------------
def _poly(lon, lat, d):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat],
                                                [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


def test_aoi_area_is_geodesic_and_estimates_are_returned(client):
    r = client.post("/api/v1/aois", json={"name": "Durgapur", "geometry": _poly(87.3, 23.5, 0.02)})
    assert r.status_code == 201, r.text
    b = r.json()
    assert 4.0 < b["area_km2"] < 5.5         # ~2.0 x 2.2 km at this latitude
    assert b["utm_crs"] == "EPSG:32645"
    assert b["estimated_sr_px"]["width"] == b["estimated_lr_px"]["width"] * 4


def test_oversized_aoi_is_blocked_before_any_work(client):
    r = client.post("/api/v1/aois", json={"name": "too big", "geometry": _poly(87.0, 23.0, 0.5)})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AOI_TOO_LARGE"
    assert r.json()["error"]["details"]["max_km2"]


# --- the full pipeline -----------------------------------------------------
@pytest.fixture(scope="module")
def completed_job(client, tmp_path_factory):
    scene = make_scene(tmp_path_factory.mktemp("e2e") / "aoi.tif", size=128)
    scene_id = _upload(client, scene).json()["scene_id"]
    r = client.post("/api/v1/jobs", json={
        "scene_id": scene_id, "models": ["bicubic"],
        "options": {"compute_metrics": True, "compute_uncertainty": True,
                    "analyses": ["ndvi"]}})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    status = client.get(f"/api/v1/jobs/{job_id}/status").json()
    assert status["status"] in ("COMPLETED", "FAILED"), status
    return job_id, status


def test_job_completes_through_every_stage(completed_job):
    job_id, status = completed_job
    assert status["status"] == "COMPLETED", status.get("error")
    assert status["progress"] == 1.0
    names = {s["name"]: s["status"] for s in status["stages"]}
    assert names["PREPROCESSING"] == "done"
    assert names["INFERENCE"] == "done"
    assert names["SCENE_ANALYSIS"] in ("done", "skipped")


def test_uncertainty_is_honestly_skipped_for_a_single_model(completed_job, client):
    """One model cannot produce disagreement. It must say so, not invent a map."""
    job_id, status = completed_job
    stage = next(s for s in status["stages"] if s["name"] == "UNCERTAINTY")
    assert stage["status"] == "skipped"
    assert stage["reason"] == "NEEDS_MULTIPLE_RUNS"
    unc = client.get(f"/api/v1/jobs/{job_id}/results").json()["uncertainty"]
    assert unc["available"] is False
    assert unc["reason"] == "NEEDS_MULTIPLE_RUNS"


def test_results_meet_the_problem_statement(completed_job, client):
    job_id, _ = completed_job
    body = client.get(f"/api/v1/jobs/{job_id}/results").json()
    run = body["runs"][0]
    assert run["status"] == "completed"
    assert run["output"]["gsd_m"] < 4.0                      # PS 26142 requirement
    assert run["geospatial_integrity"]["crs_preserved"] is True
    assert run["geospatial_integrity"]["meets_ps_requirement"] is True
    assert run["output"]["crs"] == "EPSG:32645"


def test_full_reference_metrics_are_null_with_a_reason(completed_job, client):
    """🚨 The central honesty guarantee: no ground truth means no PSNR — and we say why."""
    job_id, _ = completed_job
    metrics = client.get(f"/api/v1/jobs/{job_id}/results").json()["runs"][0]["metrics"]
    assert metrics["full_reference"] is None
    reason = metrics["full_reference_unavailable_reason"]
    assert reason and "ground truth" in reason.lower()
    # No-reference metrics ARE available and are real numbers.
    assert metrics["no_reference"]["lr_consistency_psnr"]["value"] > 0
    assert metrics["no_reference"]["spectral_angle_vs_lr"]["value"] >= 0


def test_no_ppt_numbers_anywhere_in_the_response(completed_job, client):
    """
    The PPT metrics (PSNR +6.2 dB, SSIM +0.18, SAM -2.5, LPIPS -0.21) are labelled
    "(Example)" on the slide. They must never appear as real metric values or deltas.

    Checks metric VALUES rather than raw substrings, because legitimate data (NDVI
    histogram bin edges, for instance) can coincidentally contain those digits.
    """
    job_id, _ = completed_job
    body = client.get(f"/api/v1/jobs/{job_id}/results").json()
    ppt = {6.2, 0.18, -2.5, -0.21, 2.5}
    for run in body["runs"]:
        for group in (run["metrics"]["full_reference"] or {}, run["metrics"]["no_reference"]):
            for name, m in group.items():
                if m and m.get("value") is not None:
                    assert m["value"] not in ppt, f"{name} == PPT example value {m['value']}"
        for name, d in (run["delta_vs_baseline"] or {}).items():
            assert d["delta"] not in ppt, f"delta {name} == PPT example value"


def test_provenance_travels_with_the_response(completed_job, client):
    job_id, _ = completed_job
    body = client.get(f"/api/v1/jobs/{job_id}/results").json()
    assert "model-inferred" in body["provenance"]["disclaimer"]
    assert body["provenance"]["contract_version"] == "0.1"


def test_analysis_computed_on_both_sr_and_lr(completed_job, client):
    job_id, _ = completed_job
    analyses = client.get(f"/api/v1/jobs/{job_id}/results").json()["analyses"]
    assert analyses, "NDVI analysis was requested but not produced"
    a = analyses[0]
    assert a["type"] == "ndvi"
    assert a["computed_on"] == "both"
    assert a["sr"]["stats"] and a["lr"]["stats"]
    assert "mean_delta" in a["comparison_with_lr"]
    assert a["comparison_with_lr"]["caveat"]


def test_artifacts_are_downloadable(completed_job, client):
    job_id, _ = completed_job
    body = client.get(f"/api/v1/jobs/{job_id}/results").json()
    cog = next(a for a in body["runs"][0]["artifacts"] if a["kind"] == "sr_cog")
    assert cog["crs"] == "EPSG:32645" and cog["band_count"] == 4
    r = client.get(cog["download_url"], follow_redirects=True)
    assert r.status_code == 200
    assert r.content[:2] in (b"II", b"MM")          # TIFF magic — a real GeoTIFF
    assert len(r.content) > 1000


# --- ephemeral session model (ADR-001) -------------------------------------
def test_session_reports_exactly_what_is_stored(client, completed_job):
    body = client.get("/api/v1/session").json()
    assert body["counts"]["jobs"] >= 1
    assert body["bytes_stored"] > 0
    assert body["privacy"]["accounts"] is False
    assert "DELETE /api/v1/session" in body["privacy"]["how_to_delete_now"]


def test_another_session_cannot_see_this_ones_job(other_client, completed_job):
    """Cross-session access is 404, never 403 — ids must not be enumerable."""
    job_id, _ = completed_job
    r = other_client.get(f"/api/v1/jobs/{job_id}/results")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_clear_my_data_really_deletes_everything(isolated_client, tmp_path):
    """
    Runs in its OWN session: the whole point of this endpoint is that it destroys
    everything, which would take the shared fixtures with it.
    """
    client = isolated_client
    scene = make_scene(tmp_path / "wipe.tif", size=96)
    sid = _upload(client, scene).json()["scene_id"]
    client.post("/api/v1/jobs", json={"scene_id": sid, "models": ["bicubic"],
                                      "options": {"compute_metrics": False,
                                                  "compute_uncertainty": False}})
    before = client.get("/api/v1/session").json()
    assert before["counts"]["jobs"] >= 1

    cleared = client.delete("/api/v1/session").json()
    assert cleared["cleared"] is True

    after = client.get("/api/v1/session").json()     # a brand-new session is issued
    assert after["counts"]["jobs"] == 0
    assert after["counts"]["scenes"] == 0
    assert after["session_id"] != before["session_id"]


# --- AI analyst ------------------------------------------------------------
def test_analyst_works_without_an_api_key(client, completed_job):
    """No LLM key must degrade to a template readout, never crash."""
    job_id, _ = completed_job
    caps = client.get("/api/v1/chat/capabilities").json()
    assert caps["enabled"] is False and caps["mode"] == "template"
    assert len(caps["tools"]) >= 10

    r = client.post(f"/api/v1/jobs/{job_id}/chat",
                    json={"message": "What quality metrics are available?"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is False
    assert body["degraded_reason"] == "LLM_NOT_CONFIGURED"
    assert body["tool_calls"], "the analyst must call real tools even without an LLM"
    assert body["provenance"]["verified"] is True


def test_analyst_reports_unavailable_metrics_honestly(client, completed_job):
    job_id, _ = completed_job
    body = client.post(f"/api/v1/jobs/{job_id}/chat",
                       json={"message": "What is the PSNR?"}).json()
    assert "unavailable" in body["answer"].lower() or "ground truth" in body["answer"].lower()


def test_numeric_provenance_guard_catches_invented_numbers():
    from app.services.llm.analyst import check_numeric_provenance
    tools = [{"tool": "get_metrics", "result": {"psnr": 27.31}}]
    assert check_numeric_provenance("PSNR is 27.31 dB.", tools)["verified"] is True
    bad = check_numeric_provenance("PSNR improved by 6.2 dB to 33.51 dB.", tools)
    assert bad["verified"] is False
    assert "33.51" in bad["unverified_numbers"]


# --- reports ---------------------------------------------------------------
def test_report_bundle_contains_data_and_a_disclaimer(client, completed_job):
    import zipfile
    job_id, _ = completed_job
    r = client.post(f"/api/v1/jobs/{job_id}/report", json={"format": "zip"})
    assert r.status_code == 201, r.text
    dl = client.get(r.json()["download_url"], follow_redirects=True)
    assert dl.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
        names = z.namelist()
        assert "report.json" in names and "README.txt" in names
        assert any(n.startswith("files/") for n in names)
        assert "not directly" in z.read("README.txt").decode().lower()
        json.loads(z.read("report.json"))


def test_pdf_request_degrades_gracefully_when_weasyprint_is_absent(client, completed_job):
    job_id, _ = completed_job
    body = client.post(f"/api/v1/jobs/{job_id}/report", json={"format": "pdf"}).json()
    assert body["status"] == "completed"
    assert body["format"] in ("pdf", "json")
    if body["format"] == "json":
        assert any("WeasyPrint" in n for n in body["notes"])


# --- failure handling ------------------------------------------------------
def test_requesting_an_unavailable_model_is_a_clear_422(client, tmp_path):
    scene = make_scene(tmp_path / "m.tif", size=64)
    sid = _upload(client, scene).json()["scene_id"]
    r = client.post("/api/v1/jobs", json={"scene_id": sid, "models": ["mamba_sr"]})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["reasons"]["mamba_sr"] == "NOT_IMPLEMENTED"
    assert "bicubic" in err["details"]["available"]


def test_scene_search_without_credentials_explains_the_alternative(client):
    r = client.post("/api/v1/scenes/search", json={
        "aoi": _poly(87.3, 23.5, 0.02), "date_from": "2026-01-01", "date_to": "2026-02-01"})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "PROVIDER_NOT_CONFIGURED"
    assert err["details"]["alternative"] == "upload"
