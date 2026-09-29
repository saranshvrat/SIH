# Database

PostgreSQL in production; SQLite for local development and the test suite. The ORM is
dialect-portable, so `pytest` runs with zero services (ADR-002).

## The central decision: no users table

PixelForge stores **no permanent user data**. There is no `users` table, no profiles, no login.
Identity is an anonymous `sessions` row keyed by an `HttpOnly` cookie, and *everything* hangs
off it with an `expires_at`.

```
sessions ──┬── scenes ──── (jobs)
           ├── aois
           ├── jobs ──┬── job_events
           │          ├── model_runs ──┬── metrics
           │          │                └── analyses
           │          ├── uncertainty_maps
           │          └── reports
           ├── artifacts
           └── chat_sessions ──── chat_messages
```

Every foreign key to `sessions` is `ON DELETE CASCADE`, so deleting one row removes a user'"'"'s
entire footprint. This is verified by a test: after `DELETE /session`, every dependent table is
empty.

## Tables

| Table | Holds | TTL |
|---|---|---|
| `sessions` | Anonymous session, `expires_at`, hashed user-agent | `SESSION_TTL_HOURS` (12 h) |
| `scenes` | Source imagery metadata, band mapping, checksum | `JOB_TTL_HOURS` / `SCENE_CACHE_TTL_HOURS` |
| `aois` | Geometry as GeoJSON + bbox, geodesic area, UTM zone | 24 h |
| `jobs` | Mode, status, stage array, options, terrain, errors | 24 h |
| `job_events` | Append-only stage log | with job |
| `model_runs` | Per-model provenance, output geometry, integrity report, telemetry | with job |
| `metrics` | One row per (run, metric, scope, band) | with run |
| `uncertainty_maps` | Method, **`calibrated`**, statistics, high-uncertainty regions | with job |
| `analyses` | Index results on SR *and* LR, plus the comparison | with job |
| `artifacts` | Every file: bucket, key, checksum, dimensions, CRS, bounds | with owner |
| `reports` | Generated report records | with job |
| `chat_sessions` / `chat_messages` | Analyst history, tool calls as an audit trail | with job |

## Honesty encoded in the schema

Two columns exist specifically so that the interface cannot accidentally overstate a result:

- **`metrics.reference_type`** — `none`, `lr_self`, `worldstrat`, `user_supplied`…
  A metric without a reference type is a claim without provenance, so the column is
  non-nullable. It is what lets the results endpoint distinguish "computed against ground
  truth" from "computed against the input".

- **`uncertainty_maps.calibrated`** — defaults to `false`. It stays false until a reliability
  diagram has been produced against ground truth. The API always returns it and the UI always
  renders it next to the map.

## Geometry

Stored as `geometry_geojson JSON` plus denormalised `bbox_*` floats rather than PostGIS
geometry columns (ADR-002). Under the ephemeral model there is no cross-session spatial search
— scene discovery happens against the Copernicus STAC API, not our tables — so the only
remaining need was area, which `pyproj.Geod` computes geodesically and correctly.

PostGIS is still enabled in the compose image, so reintroducing geometry columns is a migration
rather than a re-architecture.

## Identifiers

Prefixed, time-ordered, Crockford base32: `job_01M2SN3ETJ40E0FEMR`. The prefix makes every id
self-describing in a log line, and time ordering means records sort chronologically without a
join. Generated in `core/ids.py`.

## Migrations

Alembic is configured for production. In development and tests, `init_db()` creates tables at
startup from the models.

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## Cleanup

`cleanup.sweep_expired` runs every `CLEANUP_INTERVAL_MINUTES` (default 15) and, for each
session past its TTL, deletes the storage objects first and then the row — so an interrupted
sweep leaves orphaned objects rather than orphaned rows pointing at nothing.
