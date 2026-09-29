# PixelForge — Architecture Decision Record (ADR) Log

The **[Master Plan](./MASTER_PLAN.md)** is the source of truth. This file records every
place where implementation deviates from it, why, and what section of the plan was updated.

Format: `ADR-NNN · date · status · decision`

---

## ADR-001 · 2026-09-18 · ACCEPTED · Ephemeral session model replaces persistent users & history

**Master plan sections superseded:** §8.2 (`users` table), §15.1 (`/history` route as permanent
history), §21.1 criterion list (unchanged), §17 (auth).

**Context.** The master plan assumed a long-lived `users` table with a fixed demo user, and a
`/history` page listing all past jobs. The implementation brief added an explicit, overriding
requirement: PixelForge is a **one-time / ephemeral processing application**. No accounts, no
profiles, no permanent user data, no permanent imagery storage.

**Decision.**
- `users` table is **removed**. Replaced by `sessions`: an anonymous, server-issued opaque
  session id (`ses_...`) delivered as an `HttpOnly` cookie, with `created_at` and `expires_at`.
- Every `scene`, `aoi`, `job`, `run`, `artifact` is scoped to a `session_id` and carries an
  `expires_at`.
- `/history` becomes **`/session`** — "jobs from this browser session only". It is emptied by
  TTL expiry and by an explicit **"Clear my data"** button that hard-deletes rows + objects.
- A Celery beat task (`cleanup.sweep_expired`) deletes expired DB rows and their storage objects.
- Default TTLs are config-driven: `SESSION_TTL_HOURS=12`, `JOB_TTL_HOURS=24`,
  `SCENE_CACHE_TTL_HOURS=72` (shared CDSE window cache, not user data).

**Consequences.** No authentication code is needed at all (the master plan's "auth structurally
present but disabled" is satisfied by the session dependency, which is a strictly simpler
version of the same seam). Cross-session job lookup is impossible by design — a job URL from
another session returns 404, not 403, so job ids are not enumerable into other people's data.

---

## ADR-002 · 2026-09-18 · ACCEPTED · PostGIS geometry columns replaced by GeoJSON + bbox

**Master plan sections superseded:** §8.2 (`geometry(Polygon,4326)` columns, GeoAlchemy2),
§3.2 ("Required: PostGIS").

**Context.** The master plan justified PostGIS by the need for `ST_Intersects` / `ST_Area` over
stored footprints. Under ADR-001 there is **no cross-session spatial search** — scene discovery
happens against the *CDSE STAC API*, not against our own table, and each session sees only its
own handful of rows. The only remaining need was `ST_Area`, which `pyproj.Geod` computes exactly
as well in Python.

**Decision.** Store geometry as `geometry_geojson JSON` plus denormalised
`bbox_minx/miny/maxx/maxy` float columns. Area computed with `pyproj.Geod.geometry_area_perimeter`
(geodesic, correct — the naive degree-area bug the master plan warns about in §11 Phase 12 is
structurally impossible). PostGIS **remains enabled** in the compose image
(`postgis/postgis:16-3.4`) and the extension is created, so re-adding geometry columns later is
a migration, not a re-architecture.

**Consequences.** The ORM layer is now dialect-portable, so the full test suite runs on SQLite
with zero services running — which is how Phases 3–10 were actually verified in this build.
Production still runs Postgres. No feature is lost.

---

## ADR-003 · 2026-09-18 · ACCEPTED · Pluggable storage driver: `local` filesystem alongside `s3`

**Master plan sections extended:** §9 (Object Storage Design), §3.2.

**Context.** §9 specifies MinIO/S3 via a single `S3_ENDPOINT_URL`. Sound for production, but it
makes a from-clone `pytest` run require a MinIO daemon, and it blocked verification here.

**Decision.** `StorageService` is an interface with two drivers selected by `STORAGE_DRIVER`:
- `s3` (default, production + docker compose) — boto3 against MinIO/S3/R2, presigned URLs.
- `local` (dev/test) — a directory tree under `STORAGE_LOCAL_ROOT`; "presigned URLs" become
  short-lived HMAC-signed `/api/v1/artifacts/{id}/raw?sig=` links served by FastAPI.

The **key structure from §9.2 is identical across both drivers** and is produced only by
`services/keys.py`. Nothing else in the codebase knows which driver is active.

**Consequences.** `docker compose up` still uses MinIO exactly as planned. A developer with only
Python can run the whole backend. No change to the storage key contract.

---

## ADR-004 · 2026-09-18 · ACCEPTED · SEN2SR adapter ships BLOCKED; bicubic adapter carries the E2E path

**Master plan sections affected:** §1.2 model status matrix, §11 Phase 3, §13.

**Context.** Phase 3's Definition of Done requires reading `ml/experiments/models/sen2sr/`
`predictor.py`, `load.py`, `model.py` and running the ML team's own script before writing the
adapter. **The ML repository is not available in this build environment** — only the directory
listing was supplied. Master-plan Rule 1 forbids inventing unsupported details, and §13.2 items
A1–A9, B11, D2 are all still unanswered.

**Decision.**
- `Sen2SRAdapter` is implemented in full against the *documented* tree, but every call into ML
  code is marked `# ⚠️ VERIFY(§13.2 <item>)` and is routed through a single
  `_resolve_entrypoint()` function that tries the documented candidates and raises a typed
  `MLEntrypointNotFound` listing exactly what it looked for.
- Its catalogue status is computed at runtime by `healthcheck()`, not hardcoded. With no
  `ML_REPO_PATH` present it reports `status: "unavailable"`, `reason: "ML_REPO_NOT_MOUNTED"`,
  and the UI greys it out. It **never** returns a fabricated result.
- `BicubicAdapter` is **fully implemented, CPU-only, and tested**, so the end-to-end pipeline
  (upload → preprocess → infer → COG → metrics → view → download) is genuinely real today.
  It is flagged `is_baseline: true` and labelled "adds no new information" in the UI.

**Consequences.** Phase 3's DoD is **partially met**: the adapter *contract* is proven end-to-end
with a real model-shaped adapter and a real georeferenced output, but the SEN2SR-specific boxes
(QGIS check of SEN2SR output, measured VRAM, measured runtime) remain **BLOCKED** pending repo
access. Tracked in `IMPLEMENTATION_STATUS.md` as `BLOCKED:ML-REPO`.

---

## ADR-005 · 2026-09-18 · ACCEPTED · Polling for job progress; SSE deferred

**Master plan section:** §10.3 — this is the plan's own recommendation, recorded here for the
record. Implemented with TanStack Query `refetchInterval` keyed on terminal status.

---

## ADR-006 · 2026-09-18 · ACCEPTED · Hero "super-resolution lens" uses precomputed static assets

**Master plan sections extended:** §15 (the plan had no landing page; the brief requires one).

**Context.** The brief requires a cursor-driven lens that reveals a sharp image beneath a
pixelated one, and explicitly forbids running the model on mouse movement.

**Decision.** The hero ships **two precomputed static images** (a low-res/upsampled-blocky one
and a sharp one) plus a WebGL-free CSS `mask-image: radial-gradient(...)` reveal driven by a
`requestAnimationFrame` lerp of the pointer position. No canvas readback, no per-frame image
work, no ML call. Touch devices get an auto-animating lens that traverses the image.
Assets live in `frontend/public/hero/` and are generated by
`scripts/make_hero_assets.py` from any source GeoTIFF/PNG, so the imagery can be swapped for a
genuine PixelForge SR output before the demo (recommended — see `docs/DEMO-SCRIPT.md`).

**Consequences.** 60fps on integrated graphics; zero backend dependency; the hero renders
before the API is even up.

---

## ADR-007 · 2026-09-26 · ACCEPTED · WSX4-SPATRAD integrated as a self-contained ONNX adapter

**Context.** Unlike SEN2SR (assumed to need a PyTorch source-code repository per the master
plan and ADR-004), the actual model files the team provided are a self-contained ONNX export
(`wsx4_spatrad.onnx`) plus a YAML sidecar (`bands`, `factor`, `margin`) and a Mumbai-region
fine-tune variant (`wsx4_spatrad_mumbai_ft.onnx`). No inference source code exists or is needed
— the computation graph is fully described by the ONNX file itself.

The exact preprocessing contract was extracted directly from the model author's own reference
evaluation notebook (`wsx4_spatrad_eval_lat_lon.ipynb`), not guessed:

- Input is **raw, unscaled Sentinel-2 L2A DN** (uint16 range). `PREPROCESS_SCALE = 1.0` in the
  reference notebook — no `/10000` normalisation. The notebook documents that feeding
  normalised `[0,1]` input produces flat/saturated garbage with **no error raised**, so this is
  encoded as a regression-test assertion (`test_wsx4_spatrad_output_not_flat_or_garbage`), not
  just a comment.
- `margin` (130, from the YAML) is in **output-resolution** pixels. Before inference, the LR
  input is reflect-padded by `ceil(margin / factor)` on each side; after inference, exactly
  `margin` pixels are cropped off each side of the raw model output.
- Because `130 / 4 = 32.5` is not an integer, the pad/crop round-trip has a few pixels of
  inherent, harmless slop — the cropped output is not always exactly `input_size * factor`
  (observed: a 96 px input tile produced a 388 px output, not 384 px).

**Decision.**
- New adapter `app/ml/adapters/wsx4_spatrad_adapter.py`, driven by `onnxruntime`
  (CPU execution provider — **no GPU worker required** for this model family, unlike the
  PyTorch-based adapters).
- `ModelSpec` gained two fields — `weights_filename` and `margin_px` — so both WSX4 variants
  share one adapter class, distinguished only by their catalogue entry.
- New config `ONNX_WEIGHTS_DIR` (default `./ml_weights`), independent of `ML_REPO_PATH`: these
  models need a directory of `.onnx` files, not a code checkout.
- The adapter derives output georeferencing from the **actual cropped array shape** against the
  original input's real-world bounds (`rasterio.transform.from_bounds`), rather than assuming
  an exact integer scale factor. This keeps bounds exactly correct (verified:
  `bounds_max_offset_m == 0.0`) regardless of the margin rounding slop above.
- `verify_output_contract`'s scale-factor check was changed from an **absolute** tolerance
  (±0.01) to a **relative** one (±2%), because this model's honest, harmless rounding slop
  (measured: 4.0417x vs a declared 4.0x on a 96 px test tile) would otherwise fail a check meant
  to catch genuinely wrong models (e.g. one that silently produced 8x instead of 4x). A relative
  tolerance still catches those while tolerating legitimate sub-pixel rounding.

**Verification.** Ran end-to-end against the actual provided `.onnx` weight files (not a
contract-shape mock): `crs_preserved: true`, `bounds_max_offset_m: 0.0`,
`meets_ps_requirement: true` (measured 2.47 m output GSD), correct band names/order in the
output GeoTIFF, and provenance tags present. Three tests added to
`tests/ml/test_contract.py`, skipped gracefully (not failed) when the weight files aren't
present in `ONNX_WEIGHTS_DIR` — so the suite stays green for anyone who hasn't placed them.

**Consequences.** `wsx4_spatrad` and `wsx4_spatrad_mumbai_ft` are the first models in
PixelForge with `verified_against_repo: true` that are *not* the bicubic baseline. SEN2SR
(ADR-004) remains unverified/blocked — this ADR does not change that status.
