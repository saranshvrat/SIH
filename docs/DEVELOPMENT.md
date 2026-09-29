# Development

## Day-to-day loop

Two terminals, no Docker:

```bash
# terminal 1
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend && npm run dev
```

`--reload` restarts on every save. Vite hot-reloads the frontend without a refresh. No Redis
means jobs run inline in a background thread — same `JobPipeline`, so what you see locally is
what runs in Celery.

## Testing

```bash
cd backend
python -m pytest tests -q                 # everything, ~15s, no services required
python -m pytest tests/ml -q               # ML contract only
python -m pytest tests/api -q              # full HTTP E2E
python -m pytest tests/api/test_e2e.py::test_full_reference_metrics_are_null_with_a_reason -v
```

All fixtures are synthetic (`tests/fixtures/make_fixtures.py`) — deterministic Sentinel-2-like
GeoTIFFs generated in-memory. No network, no ML repo, no external files.

The **acceptance harness** for the ML seam runs outside pytest entirely:

```bash
python scripts/smoke_adapter.py --model bicubic --input any.tif --out ./out
python scripts/smoke_adapter.py --list      # what's actually runnable right now
```

Frontend:

```bash
cd frontend
npm run typecheck
npm run build          # catches anything typecheck alone misses
```

## Adding a model

1. `backend/app/ml/adapters/<name>_adapter.py` implementing the `SRModelAdapter` protocol
   (see `ml/contracts.py`). Mark every call into unverified ML code with
   `# ⚠️ VERIFY(...)` if you haven't confirmed it against the real repo yet.
2. One entry in `backend/app/ml/model_catalog.yaml`.
3. `python scripts/smoke_adapter.py --model <name> --input test.tif --out ./out`
4. Add a parametrised case to `tests/ml/test_contract.py`.

No frontend change needed — `GET /api/v1/models` drives the UI.

## Adding an API route

1. Schema in `app/api/v1/schemas/`, route in `app/api/v1/routes/`.
2. Register it in `app/main.py`'s router loop.
3. Business logic goes in `app/domains/` or `app/services/`, never inline in the route
   function — routes should stay thin enough to read in one glance.
4. A test in `tests/api/test_e2e.py` or a new file under `tests/api/`.

## Code style

- Backend: `ruff check app tests`. Type hints everywhere; `from __future__ import annotations`
  at the top of every module.
- Frontend: `npm run typecheck`. Components are function components; shared logic goes in
  `api/queries/hooks.ts`, not duplicated per-component.
- Comments explain **why**, not what — the "what" should be obvious from the code. Look at
  any file under `app/ml/` or `app/domains/` for the tone we're going for.

## Database changes

```bash
cd backend
alembic revision --autogenerate -m "add analysis.confidence column"
alembic upgrade head
```

In dev/test, `init_db()` just creates tables fresh from the models — no migration needed for
local iteration. Alembic matters once there's data in a real Postgres you don't want to lose.

## Debugging a stuck job

```bash
curl -s localhost:8000/api/v1/jobs/<id>/status | python3 -m json.tool
```

Look at `stages[].reason` for any `skipped` stage, and `error.code` if `status == FAILED`.
Structured logs are JSON keyed by `request_id` / `job_id` — grep for the job id across a
`docker compose logs` stream to reconstruct the full lifecycle.

## Regenerating the hero assets

```bash
python scripts/make_hero_assets.py --input <sharp.tif> --lr <real_10m.tif> \
    --out frontend/public/hero
```

Always pass `--lr` with a genuine low-resolution counterpart before a demo — without it the
"blocky" image is a downsampled analogy, which the generator records in `SOURCE.txt` so
nobody mistakes it for a real observation.
