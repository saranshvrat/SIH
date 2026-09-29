# Environment Variables

Full reference. See `.env.example` for the file to copy. Grouped by what they unlock, matching
the sections in that file.

## Core

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `test` \| `production`. Controls whether `init_db()` auto-creates tables (never in production). |
| `LOG_LEVEL` | `INFO` | Standard Python levels. |
| `LOG_JSON` | `true` | `false` for human-readable console logs in local dev. |
| `API_V1_PREFIX` | `/api/v1` | Change only if you're proxying under a different path. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:4173` | Comma-separated. Must be exact in production. |
| `SECRET_KEY` | dev placeholder | Signs local-driver download URLs. **Change in production.** |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./pixelforge.db` | SQLAlchemy URL. SQLite needs no setup; Postgres needs `psycopg[binary]` (already in requirements). |

## Async processing

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Used for the health check display only. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Separate DB index from result backend by convention. |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `true` makes jobs run synchronously in-process — used by the test suite. Don't set this in a real deployment. |

Without a reachable broker, `dispatch_job()` falls back to an in-process background thread
automatically — these variables don't need to be unset to get that behaviour, it happens
whenever Redis can't be reached.

## Object storage

| Variable | Default | Notes |
|---|---|---|
| `STORAGE_DRIVER` | `local` | `local` (filesystem) or `s3` (MinIO/AWS/R2). |
| `STORAGE_LOCAL_ROOT` | `./data/storage` | Only used when `STORAGE_DRIVER=local`. |
| `S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO's default. Point at your real endpoint for AWS/R2. |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` | **Change for anything beyond local dev.** |
| `S3_REGION` | `us-east-1` | |
| `S3_BUCKET_SCENES` / `S3_BUCKET_JOBS` / `S3_BUCKET_REPORTS` | `pixelforge-*` | Created automatically on first boot if missing (`S3Driver.ensure_buckets`). |
| `S3_PRESIGN_TTL` | `900` | Seconds a download link stays valid. |

## Ephemeral data model

| Variable | Default | Notes |
|---|---|---|
| `SESSION_COOKIE_NAME` | `pf_session` | |
| `SESSION_TTL_HOURS` | `12` | How long an idle browser session survives. |
| `JOB_TTL_HOURS` | `24` | How long a specific job's data survives, independent of session activity. |
| `SCENE_CACHE_TTL_HOURS` | `72` | Catalogue scene metadata cache (not user-specific data). |
| `CLEANUP_INTERVAL_MINUTES` | `15` | How often the beat sweeper runs. |

## Machine learning

| Variable | Default | Notes |
|---|---|---|
| `ML_REPO_PATH` | `/ml` | Filesystem path to the ML team's repository. Unlocks SEN2SR when correct. |
| `ML_WEIGHTS_PATH` | `/weights` | Currently informational; weights are located relative to `ML_REPO_PATH` inside the adapter. |
| `ML_COMMIT` | `unknown` | Recorded into every output GeoTIFF's provenance tags — set this to the real commit hash you're running. |
| `ML_DEVICE` | `cuda` | `cuda` or `cpu`. |
| `ML_DEFAULT_MODEL` | `bicubic` | Used when a job doesn't specify a model and auto-select is off. |
| `ML_KEEP_MODELS_RESIDENT` | `true` | Informational; the registry caches loaded adapters per-process regardless. |

## Sentinel-2 catalogue (satellite imagery search)

The Explore page's map-based Sentinel-2 search is backed by
[Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)'s public STAC
API (`app/services/catalog/planetary_computer.py`) — a public catalogue that needs no
account, no client credentials, and no OpenID token exchange. There is nothing to
configure and no variable to set; `POST /scenes/search` always works, subject only to
Planetary Computer's own availability.

The original Copernicus Data Space Ecosystem (CDSE) client is still in the tree
(`app/services/catalog/cdse.py`, `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` in `.env`) but is
no longer imported anywhere. Point `routes/scenes.py` and `domains/pipeline.py` back at it
if you'd rather use CDSE.

## AI analyst

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `none` | `anthropic` \| `openai` \| `none`. |
| `LLM_API_KEY` | empty | |
| `LLM_MODEL` | `claude-sonnet-4-6` | |
| `LLM_MAX_TOKENS` | `1024` | |
| `LLM_TEMPERATURE` | `0.1` | Kept low deliberately — this is a data-reporting assistant, not a creative one. |

`LLM_PROVIDER=none` (or a missing key) is a fully supported configuration: the analyst still
calls every backend tool and returns a direct data readout instead of LLM-generated prose.

## Limits

| Variable | Default | Notes |
|---|---|---|
| `MAX_UPLOAD_MB` | `200` | Must be ≤ the reverse proxy's body size limit. |
| `MAX_AOI_KM2` | `25` | Area of interest cap, enforced before any processing starts. |
| `MAX_INPUT_PX` | `512` | Global fallback; individual models may set a lower `max_input_px` in the catalogue. |
| `JOB_SOFT_TIME_LIMIT` / `JOB_TIME_LIMIT` | `1500` / `1800` | Seconds. Celery soft/hard task timeouts. |
