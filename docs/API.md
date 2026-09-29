# API Reference

Base path `/api/v1`. Interactive docs at `/docs`. All responses are JSON.

## Authentication

There is none. Identity is an anonymous session issued as an `HttpOnly` cookie
(`pf_session`) on first request. Every resource is scoped to it, and a request for another
session'"'"'s resource returns **404** — not 403, so ids cannot be probed.

## Error envelope

Every failure, everywhere, has this shape:

```json
{ "error": {
    "code": "INVALID_RASTER_GSD",
    "message": "Input ground sample distance is 30.0 m, but the selected model expects…",
    "details": { "detected_gsd_m": [30.0, 30.0], "expected_gsd_m": 10.0 },
    "retryable": false,
    "request_id": "req_01M2T…" } }
```

Branch on `code`, never on `message`. `request_id` appears in the structured logs.

### Codes

| Code | HTTP | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | Absent, or belongs to another session |
| `VALIDATION_ERROR` | 422 | Malformed request |
| `CONFLICT` | 409 | Wrong state (e.g. band mapping unconfirmed) |
| `FILE_TOO_LARGE` | 413 | Above `MAX_UPLOAD_MB` |
| `UNSUPPORTED_FORMAT` | 422 | Not a GeoTIFF/JPEG2000 |
| `NOT_GEOREFERENCED` | 422 | No affine transform |
| `MISSING_CRS` | 422 | No coordinate reference system |
| `INVALID_RASTER_GSD` | 422 | Resolution far from the model'"'"'s expectation |
| `INSUFFICIENT_BANDS` | 422 | Fewer bands than the model needs |
| `CORRUPT_RASTER` | 422 | Could not be opened |
| `AOI_TOO_LARGE` | 422 | Above `MAX_AOI_KM2` |
| `MODEL_UNAVAILABLE` | 422 | Model cannot run; `details.reason` says why |
| `PROVIDER_NOT_CONFIGURED` | 503 | Missing credentials; `details.alternative` suggests a path |
| `SOURCE_UNAVAILABLE` | 502 | Upstream provider failed (retryable) |
| `GPU_OUT_OF_MEMORY` | 500 | Reduce area or `MAX_INPUT_PX` (retryable) |

## System

### `GET /health`
```json
{ "status": "ok", "database": true, "redis": false,
  "storage": { "ok": true, "driver": "local" },
  "gpu_workers_online": 0, "models_available": ["bicubic"],
  "features": { "cdse": false, "llm": false, "async_workers": false },
  "contract_version": "0.1" }
```

### `GET /models`
The contract that keeps the frontend model-agnostic. `status` is computed at runtime — a model
declared available whose weights are missing is reported `unavailable` with a reason.

```json
{ "models": [
    { "key": "bicubic", "display_name": "Bicubic (baseline)", "status": "available",
      "is_baseline": true, "output_gsd_m": 2.5, "scale_factor": 4,
      "verified_against_repo": true },
    { "key": "sen2sr", "status": "unavailable",
      "unavailable_reason": "ML_REPO_NOT_MOUNTED", "verified_against_repo": false },
    { "key": "mamba_sr", "status": "unavailable",
      "unavailable_reason": "NOT_IMPLEMENTED" } ],
  "default_model": "bicubic", "contract_version": "0.1" }
```

## Areas of interest

### `POST /aois/preview`
Size and cost estimate without persisting. Powers the live readout while drawing.

### `POST /aois` → 201
```json
{ "aoi_id": "aoi_01M…", "area_km2": 4.84, "utm_crs": "EPSG:32645",
  "estimated_lr_px": { "width": 204, "height": 222 },
  "estimated_sr_px": { "width": 816, "height": 888 },
  "estimated_output_mb": 5.5, "warnings": [] }
```
Area is geodesic (`pyproj.Geod`), so the classic degrees-as-metres error is impossible.

## Scenes

### `POST /scenes/search`
Requires CDSE credentials; otherwise `503 PROVIDER_NOT_CONFIGURED` with
`details.alternative: "upload"`. Note that `cloud_cover` is **scene-level** — the whole ~110 km
tile — and `aoi_cloud_cover` is `null` until preprocessing measures it.

### `POST /scenes/upload` → 201
`multipart/form-data`: `file`, optional `force=true` to override a GSD mismatch.
Streamed with a running byte cap. Returns the full validation report, the detected band
mapping, and `band_mapping_confirmed`. When that is `false`, `status` is `needs_confirmation`
and job creation is refused until confirmed.

### `PATCH /scenes/{id}/bands`
```json
{ "band_mapping": { "1": "B02", "2": "B03", "3": "B04", "4": "B08" } }
```

## Jobs

### `POST /jobs` → 202
```json
{ "scene_id": "scn_…", "aoi_id": "aoi_…", "models": ["bicubic", "sen2sr"],
  "auto_select_model": false,
  "options": { "cloud_mask": true, "compute_metrics": true,
               "compute_uncertainty": true, "analyses": ["ndvi"] },
  "idempotency_key": "optional-client-key" }
```
Supplying `idempotency_key` makes retries safe: the original job is returned with
`already_existed: true`.

### `GET /jobs/{id}/status`
Poll this. Stop when `status` is `COMPLETED`, `FAILED` or `CANCELLED`.

```json
{ "status": "INFERENCE", "progress": 0.62, "current_stage": "INFERENCE",
  "stages": [
    { "name": "DOWNLOADING", "status": "skipped", "reason": "UPLOADED_SOURCE",
      "reason_text": "This scene was uploaded, so no download was needed." },
    { "name": "INFERENCE", "status": "running", "progress": 0.4,
      "detail": "bicubic · tile 3/9" },
    { "name": "UNCERTAINTY", "status": "skipped", "reason": "NEEDS_MULTIPLE_RUNS",
      "reason_text": "Uncertainty from model disagreement needs at least two model outputs." } ],
  "terrain": { "class": "agricultural", "confidence": 0.57 } }
```

Every skipped stage carries both a code and a sentence. There are no silent gaps.

## Results

### `GET /jobs/{id}/results`

The honesty contract lives here.

```json
{ "runs": [{
    "model_display_name": "Bicubic (baseline)",
    "output": { "gsd_m": 2.5, "scale_factor": 4.0, "crs": "EPSG:32645" },
    "geospatial_integrity": {
      "crs_preserved": true, "bounds_max_offset_m": 0.0,
      "scale_factor_actual": 4.0, "meets_ps_requirement": true },
    "metrics": {
      "full_reference": null,
      "full_reference_unavailable_reason":
        "No high-resolution ground truth exists for this area, so PSNR, SSIM, SAM and RMSE cannot be computed…",
      "no_reference": {
        "lr_consistency_psnr": { "value": 31.18, "unit": "dB", "explanation": "…" },
        "spectral_angle_vs_lr": { "value": 0.38, "unit": "deg", "explanation": "…" } } } }],
  "uncertainty": { "available": false, "reason": "NEEDS_MULTIPLE_RUNS",
                   "reason_text": "…needs at least two model outputs." },
  "provenance": { "disclaimer": "Super-resolved imagery contains model-inferred detail…" } }
```

**`full_reference: null` is a deliberate answer, not missing data.** Without ground truth these
metrics are impossible, and PixelForge says so rather than showing a comfortable number.

When uncertainty *is* available it always carries `calibrated` (false until validated against
ground truth) and `confidence_definition` stating exactly what the number means.

## Analysis

`GET /analyses/available` · `POST /jobs/{id}/analyses` with `{"type": "ndvi"}`.

Every index is computed on **both** the super-resolved output and the original observation, and
the response includes `comparison_with_lr` — the delta plus a caveat. An index computed only on
a reconstruction is an unfalsifiable claim; the comparison makes it checkable.

## AI analyst

### `GET /chat/capabilities`
Reports `enabled`, `mode` (`llm` or `template`), the tool list, and suggested questions.
**Without an API key the analyst still works**, calling the same real tools and returning a
direct data readout.

### `POST /jobs/{id}/chat`
```json
{ "answer": "…", "llm_used": false, "degraded_reason": "LLM_NOT_CONFIGURED",
  "tool_calls": [{ "tool": "get_metrics", "input": {}, "result": { … } }],
  "provenance": { "verified": true, "unverified_numbers": [] } }
```
`tool_calls` powers the transparency panel. `provenance.verified` is false if the answer
contains a number that did not appear in any tool result — an audit signal surfaced to the user.

## Session and export

- `GET /session` — exactly what is stored, counts, bytes, TTL and the privacy statement.
- `DELETE /session` — hard-deletes every row and object. Irreversible.
- `POST /jobs/{id}/report` — `{"format": "zip" | "json" | "pdf"}`. ZIP and JSON always work;
  PDF degrades to JSON with a note when WeasyPrint is absent.
- `GET /artifacts/{id}/download` — redirects to a presigned (S3) or HMAC-signed (local) URL.
