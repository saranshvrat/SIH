# PixelForge

**AI-powered super-resolution and geospatial intelligence for medium-resolution satellite imagery.**

Smart India Hackathon 2026 · Problem statement **26142** (NTRO) —
*Deep Learning Based Super Resolution Mapping from Medium Resolution Satellite Imageries.*

---

## What is PixelForge?

Sentinel-2 images every point on Earth every five days, free, at **10 metres per pixel**. At
that scale a narrow road, a single dwelling or a field boundary occupies a fraction of a pixel
and is simply not there.

PixelForge reconstructs that detail with deep learning, targeting **better than 4 metres**,
while keeping the output a real geospatial product — correct coordinate system, correct
footprint, spectrally consistent with what the sensor actually measured.

It also does something most super-resolution demos do not: **it tells you where it is guessing.**

## The problem

> Medium-resolution satellite imagery is widely available but lacks the spatial detail needed
> for fine-scale analysis. High-resolution imagery is costly and infrequent. Develop a deep
> learning based super-resolution approach that enhances 10 m imagery to below 4 m while
> preserving geospatial and spectral consistency, with explicit management of uncertainty and
> validation against high-resolution references.

## How it works

```
        ┌── map AOI ──┐
        │             ├─→ validate ─→ preprocess ─→ characterise ─→ SR inference ─→ verify
        └── upload ───┘       │            │             │                │            │
                              │            │             │                │            ↓
                         typed errors  band order   spectral        tiled + blended  CRS · bounds
                                       enforced     indices                          · scale · <4 m
                                                                                          │
                          ┌───────────────────────────────────────────────────────────────┘
                          ↓
              quality assessment ─→ uncertainty ─→ geospatial analysis ─→ dashboard
                          │              │                 │                   │
                    vs observation  model disagreement  NDVI on SR AND LR   compare · metrics
                    (no ground truth      (only when 2+     + the delta      · uncertainty
                     required)          models actually run)                · AI analyst · export
```

## Features

- **Two ways in** — draw an area of interest on a map and pull a Sentinel-2 scene from
  Copernicus, or upload your own georeferenced product. Both converge on one pipeline.
- **Real validation** — files are validated by opening them with rasterio, not by extension.
  Nine typed error codes, each with an explanation a user can act on.
- **Band-order safety** — the mapping from file bands to spectral bands is detected through a
  four-level ladder and confirmed by the user when it cannot be determined. A silent band
  swap produces a plausible-looking, scientifically wrong result, so this is treated as a
  first-class correctness problem.
- **Live pipeline view** — ten stages with real backend status. Stages that are skipped say
  why, in words.
- **Output contract verification** — every model output is checked for CRS preservation,
  footprint drift, scale factor, band count and the problem statement's <4 m requirement
  before it is allowed to reach the user.
- **Model comparison** — synchronised swipe, side-by-side and single-pane viewing, with an
  identical contrast stretch across every pane, so visible differences are model differences.
- **Honest metrics** — full-reference metrics (PSNR/SSIM/SAM/RMSE) are computed where ground
  truth exists and returned as `null` **with an explanation** where it does not. No-reference
  metrics, which need no ground truth, are always available.
- **Uncertainty from genuine disagreement** — computed only from two or more independent model
  outputs, always labelled uncalibrated until validated. Never derived from edge detection or
  local variance.
- **Geospatial analysis** — NDVI/NDWI/NDBI computed on *both* the reconstruction and the
  original observation, with the difference reported.
- **AI Satellite Analyst** — a contextual analyst with eleven tools over this job's real data,
  a numeric-provenance guard, and a transparency panel showing exactly which tools produced
  each answer. Works without an API key.
- **Ephemeral by design** — no accounts, no permanent storage, automatic TTL cleanup, and a
  page showing exactly what is held with a button that destroys it.

## Architecture

A **modular monolith**. One FastAPI application, split by domain, with Celery for long work.

```
React + TypeScript ──→ FastAPI ──┬──→ PostgreSQL     (job metadata, TTL-scoped)
                                 ├──→ Redis ──→ Celery ──┬── cpu queue  (download, prep, metrics)
                                 │                       └── gpu queue  (inference, --pool=solo)
                                 └──→ MinIO / S3    (rasters, previews, reports)
```

The ML layer sits behind a frozen adapter contract (`backend/app/ml/contracts.py`). Nothing
above it imports PyTorch; nothing below it knows about HTTP. Adding a model is one YAML entry
plus one adapter file — **zero frontend changes**, because the interface renders itself from
`GET /api/v1/models`.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind, MapLibre GL, TanStack Query |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 |
| Geospatial | rasterio / GDAL, pyproj, shapely |
| Async | Celery + Redis (two queues) |
| Database | PostgreSQL + PostGIS (SQLite for local dev and tests) |
| Storage | MinIO / S3-compatible (local filesystem driver for dev) |
| ML | PyTorch, via the PixelForge ML repository |
| Deployment | Docker Compose |

## Project structure

```
pixel-forge/
├── backend/
│   ├── app/
│   │   ├── ml/              adapter contract, registry, output verification
│   │   │   └── adapters/    one file per model — all model-specific knowledge lives here
│   │   ├── services/        raster, preprocessing, metrics, uncertainty, geo, catalog, llm
│   │   ├── domains/         job pipeline, analysis, session lifecycle
│   │   ├── api/v1/          routes + schemas
│   │   ├── tasks/           Celery app, dispatch, cleanup
│   │   ├── db/              SQLAlchemy models
│   │   └── core/            config, logging, errors, ids
│   ├── scripts/             smoke_adapter.py — run one model with no services
│   └── tests/               ml/ api/ unit/ + synthetic fixtures
├── frontend/src/
│   ├── components/hero/     the super-resolution lens
│   ├── components/map/      MapLibre canvas + AOI drawing
│   ├── components/viewer/   synchronised comparison viewer
│   ├── features/            landing, explore, processing, results, chat
│   └── api/                 typed client + React Query hooks
├── infrastructure/compose/  the full stack
├── docs/                    MASTER_PLAN, DECISIONS (ADRs), ARCHITECTURE, API, ML-INTEGRATION…
└── ml/                      the ML repository — mounted, not vendored
```

## Prerequisites

| | Required | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| Node | 20+ | 22 recommended |
| Docker | optional | only for the full stack |
| NVIDIA GPU + CUDA 12 | optional | needed only for SEN2SR; the baseline runs on CPU |

**Nothing else is required to run PixelForge.** With no database, no Redis and no MinIO it
falls back to SQLite, in-process jobs and local filesystem storage.

## Installation

```bash
git clone <your-repo-url> pixel-forge
cd pixel-forge
cp .env.example .env
```

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
```

Frontend:

```bash
cd frontend
npm install
```

Or do both with `make setup`.

## Environment variables

**The `.env` file goes in the repository root**, next to `.env.example`. Nothing needs editing
to get a working application — every entry below is about unlocking additional capability.

| Variable | Purpose | Required | Example |
|---|---|---|---|
| `SECRET_KEY` | Signs local download URLs | **In production** | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` | Database | No | `sqlite:///./pixelforge.db` |
| `REDIS_URL` / `CELERY_BROKER_URL` | Async queues | No | `redis://localhost:6379/0` |
| `STORAGE_DRIVER` | `local` or `s3` | No | `local` |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Object storage | If `STORAGE_DRIVER=s3` | `http://localhost:9000` |
| **`LLM_API_KEY`** | **AI analyst prose** | No | `sk-ant-…` |
| `LLM_PROVIDER` | `anthropic`, `openai` or `none` | No | `anthropic` |
| **`ML_REPO_PATH`** | **Path to the ML repository** | **For SEN2SR** | `./ml` |
| `ML_DEVICE` | `cuda` or `cpu` | No | `cuda` |
| `SESSION_TTL_HOURS` / `JOB_TTL_HOURS` | Data retention | No | `12` / `24` |
| `MAX_UPLOAD_MB` / `MAX_AOI_KM2` | Request limits | No | `200` / `25` |

**Where credentials go:**
- **Satellite imagery** → nothing to configure. Map-based Sentinel-2 search reads from
  Microsoft Planetary Computer's public STAC catalogue, which needs no account. (The
  original CDSE client is still in the tree, unused — see `docs/ENVIRONMENT.md` if you'd
  rather switch back to it.)
- **LLM** → `LLM_API_KEY` plus `LLM_PROVIDER`. Without it the analyst still runs every backend
  tool and returns a direct data readout instead of prose.
- **Object storage** → `S3_ACCESS_KEY` / `S3_SECRET_KEY`. Not needed with `STORAGE_DRIVER=local`.

Never commit `.env` — it is in `.gitignore`.

## Running locally

Two terminals, no Docker, no services:

```bash
# terminal 1 — API on http://localhost:8000
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal 2 — UI on http://localhost:5173
cd frontend && npm run dev
```

Open **http://localhost:5173**. Jobs run in-process because no Celery broker is reachable —
the pipeline is identical, it simply is not distributed.

With Redis available, add workers so jobs run properly asynchronously:

```bash
# terminal 3
cd backend && celery -A app.tasks.celery_app worker -Q cpu,gpu --pool=solo -l info
# terminal 4 — TTL cleanup
cd backend && celery -A app.tasks.celery_app beat -l info
```

## Running with Docker

```bash
cp .env.example .env
docker compose -f infrastructure/compose/docker-compose.yml up -d --build
```

| Service | URL |
|---|---|
| Application | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |

With an NVIDIA GPU and the container toolkit installed, add the GPU worker:

```bash
docker compose -f infrastructure/compose/docker-compose.yml --profile gpu up -d --build
```

Stop with `docker compose -f infrastructure/compose/docker-compose.yml down`.

## ML model setup

The ML models live in a **separate repository owned by the ML team**. PixelForge integrates it
read-only and never modifies it.

```bash
git submodule add <ml-repo-url> ml
git submodule update --init --recursive
echo "ML_REPO_PATH=./ml" >> .env
```

Verify the integration before trusting any output:

```bash
cd backend
python scripts/smoke_adapter.py --list                      # what can actually run
python scripts/smoke_adapter.py --model sen2sr \
    --input path/to/patch.tif --out ./out
```

This prints the full contract verification — CRS preservation, footprint drift, measured scale
factor, output GSD against the <4 m requirement, and a spectral-consistency check.

> **⚠️ The SEN2SR adapter ships unverified.** It was written against the documented repository
> layout, but the repository was not available when it was built. Every call into ML code is
> marked `# ⚠️ VERIFY(§13.2 …)`. Until it is confirmed, `GET /api/v1/models` reports SEN2SR as
> `unavailable` with a reason, and the UI greys it out. See **[docs/ML-INTEGRATION.md](docs/ML-INTEGRATION.md)**
> and ADR-004 in **[docs/DECISIONS.md](docs/DECISIONS.md)**.
>
> The **bicubic baseline** is fully implemented and tested, so the complete pipeline works today.

## Using PixelForge

1. **Open the application.** The hero demonstrates the effect — move your cursor across the
   image to reveal reconstructed detail beneath the low-resolution observation.
2. **Choose your input.** *Explore* to draw an area on the map, or *Process* to upload a file.
3. **Draw an area of interest.** Rectangle or polygon. Area, pixel dimensions, output size and
   projection update live; areas that are too large are rejected before any work starts.
4. **Select a scene.** Search Sentinel-2 by date and cloud cover, then pick an acquisition.
5. **Choose models.** Pick two or more to obtain an uncertainty map from their disagreement.
   Unavailable models are shown with the reason rather than hidden.
6. **Watch it run.** Ten real pipeline stages; skipped stages explain themselves.
7. **Compare.** Swipe, side-by-side or single pane, with synchronised zoom and pan.
8. **Read the metrics.** Note which are computable for your area, and which are not — and why.
9. **Check the uncertainty.** Where models disagree, reconstruction is ambiguous.
10. **Run an analysis.** NDVI, NDWI or NDBI, computed on the reconstruction *and* the original.
11. **Ask the analyst.** "Compare the models and explain the uncertainty." Expand *data used*
    to see exactly which tools produced the answer.
12. **Export.** A ZIP bundle with every raster as a COG carrying provenance in its tags, plus
    the original observation so results can always be checked.

## Data privacy

PixelForge is a **one-time processing application**.

- **No accounts, no profiles, no login.** Identity is an anonymous session cookie.
- **What is stored:** job metadata, the imagery you selected or uploaded, and the outputs
  produced from it.
- **Where:** the application's database and object storage.
- **How long:** until the session expires (`SESSION_TTL_HOURS`, default 12 h) or
  `JOB_TTL_HOURS` after each job (default 24 h), whichever is sooner.
- **How cleanup happens:** a Celery beat task runs every 15 minutes and hard-deletes expired
  sessions along with every row and every stored object belonging to them.
- **Delete immediately:** the *Your data* page shows exactly what is held and clears it on
  request (`DELETE /api/v1/session`).
- Results are visible **only to the browser session that created them**. Another session
  requesting the same job id gets `404`, not `403`, so job ids cannot be probed.

## API

Interactive documentation at **http://localhost:8000/docs** (ReDoc at `/redoc`).

```bash
curl http://localhost:8000/api/v1/health     # service + model availability
curl http://localhost:8000/api/v1/models     # live model catalogue
```

Generate TypeScript types from the running server:

```bash
npx openapi-typescript http://localhost:8000/openapi.json -o frontend/src/types/schema.d.ts
```

Full reference: **[docs/API.md](docs/API.md)**.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `NETWORK_ERROR` in the UI | Backend is not running. Start it on port 8000. |
| Only "Bicubic" is selectable | Expected without the ML repository. `python scripts/smoke_adapter.py --list` shows the exact reason per model. |
| SEN2SR says `ML_REPO_NOT_MOUNTED` | Set `ML_REPO_PATH` to your ML checkout and restart. |
| `GPU_OUT_OF_MEMORY` | Reduce `MAX_INPUT_PX`, or draw a smaller area. The GPU queue is `--pool=solo` so only one job runs at a time. |
| `INVALID_RASTER_GSD` on upload | Your file is not ~10 m. Re-submit with "Process anyway" — metrics will not be comparable to benchmarks. |
| `BAND_MAPPING_AMBIGUOUS` | Band names are absent from the file metadata. Confirm the mapping in the UI before processing. |
| Metrics show "—" everywhere | Expected in inference mode. See *Current limitations*. |
| Uncertainty tab says "needs multiple runs" | Correct behaviour with one model. Re-run with two or more. |
| Jobs stay `QUEUED` with Docker | The worker container is unhealthy. `docker compose logs worker`. |

## Current limitations

Stated plainly, because a judge will ask.

1. **Only the bicubic baseline is verified end to end.** SEN2SR's adapter is written but
   unconfirmed against the real repository; LDSR-S2 and Satlas have no committed weights.
   **Mamba SR has no implementation at all** and is listed as unavailable for transparency.
2. **Full-reference metrics need ground truth that does not exist for arbitrary areas.** On a
   real AOI, PSNR/SSIM/SAM/RMSE are *impossible*, not merely unimplemented. PixelForge returns
   `null` with an explanation. Use validation mode on a paired dataset for real numbers.
3. **LPIPS is not computed.** It needs a pretrained network not installed in the worker. It
   returns `null` with that reason rather than an approximation.
4. **Uncertainty is uncalibrated.** It measures model disagreement, which indicates *where*
   reconstruction is ambiguous but is not a probability. Calibration against ground truth is
   future work.
5. **Cloud cover shown during search is scene-level**, for the whole ~110 km tile, not your
   area. AOI-level cloud fraction is computed later, during preprocessing.
6. **Land cover is index-based**, using standard spectral thresholds — not a trained
   classifier. The interface says so.
7. **Change detection is not implemented.** It needs two acquisitions over the same area.
8. **`.SAFE` archive upload is unsupported.** Single multi-band GeoTIFF or JPEG2000 only.
9. **The bundled CDSE client is unverified and unused** — Sentinel-2 search now runs
   against Microsoft Planetary Computer instead (see `docs/ENVIRONMENT.md`).

## Future scope

Genuine next steps, in order of value:

- Verify the SEN2SR adapter against the real repository and publish measured runtime and VRAM.
- Validation mode over WorldStrat/NAIP pairs, to produce defensible PSNR/SSIM/SAM figures.
- A second learned model, which makes the uncertainty map meaningful rather than a
  model-vs-interpolation difference.
- Calibrate uncertainty against ground truth and publish a reliability diagram.
- ESA WorldCover lookup to replace the index heuristic for land-cover characterisation.
- Change detection across two acquisitions.
- Tiled COG serving so full-resolution rasters stream into the map instead of static previews.

---

**Documentation:** [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) ·
[ML integration](docs/ML-INTEGRATION.md) · [Database](docs/DATABASE.md) ·
[Development](docs/DEVELOPMENT.md) · [Deployment](docs/DEPLOYMENT.md) ·
[Environment](docs/ENVIRONMENT.md) · [Decisions](docs/DECISIONS.md) ·
[Demo script](docs/DEMO-SCRIPT.md) · [Status](IMPLEMENTATION_STATUS.md)
