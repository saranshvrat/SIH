# Deployment

## Docker Compose (reference deployment)

```bash
cp .env.example .env        # edit SECRET_KEY at minimum
docker compose -f infrastructure/compose/docker-compose.yml up -d --build
```

Services: `postgres` (PostGIS), `redis`, `minio`, `api`, `worker` (cpu queue), `beat`
(cleanup scheduler), `frontend` (nginx). `gpu-worker` is behind the `gpu` profile:

```bash
docker compose -f infrastructure/compose/docker-compose.yml --profile gpu up -d --build
```

Requires the NVIDIA container toolkit on the host. Without it, the stack still starts and the
bicubic baseline still works — SEN2SR alone needs the GPU worker.

## Before going to production

- [ ] `SECRET_KEY` — generate with `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
      Never reuse the dev placeholder; it signs local download URLs.
- [ ] `ENVIRONMENT=production` — disables the dev-only `init_db()` auto-create; run Alembic
      migrations explicitly instead.
- [ ] `CORS_ORIGINS` — the exact origin(s) the frontend is served from, not `*`.
- [ ] Database — point `DATABASE_URL` at a real, backed-up Postgres. Run
      `alembic upgrade head` once before first boot.
- [ ] Object storage — real S3/R2 credentials, not `minioadmin`.
- [ ] TLS — terminate HTTPS in front of nginx (a reverse proxy or load balancer); the app
      itself does not handle certificates.
- [ ] `MAX_UPLOAD_MB` and the nginx `client_max_body_size` must agree (nginx's is set to
      250M in the shipped config; raise both together if you increase the limit).

## Scaling

- **API**: stateless behind a load balancer. `--workers N` on uvicorn, or run more replicas.
- **cpu worker**: horizontally scalable; increase `--concurrency` or add replicas.
- **gpu worker**: one process per GPU. `--pool=solo --concurrency 1` is not a limitation to
  work around — it is what prevents two jobs from fighting over the same VRAM. Add more GPU
  worker replicas (one per physical GPU) rather than raising concurrency on one.
- **beat**: exactly one replica. Running two schedulers double-fires the cleanup task.

## Health and monitoring

`GET /api/v1/health` reports database, storage, Redis and live model availability — point
your load balancer's health check here, not at `/`.

```json
{ "status": "ok", "database": true, "storage": {"ok": true}, "redis": true,
  "models_available": ["bicubic", "sen2sr"] }
```

`status: "degraded"` means the API is up but something behind it (storage, no models
available) isn't — a signal to page someone, not necessarily to fail over immediately.

Structured JSON logs (`LOG_JSON=true`) are keyed by `request_id` and `job_id`; ship them to
whatever aggregator you have and filter on those keys to reconstruct a job's full lifecycle
across api → worker → gpu-worker.

## The TTL sweeper is not optional

`beat` runs `pixelforge.sweep_expired` every `CLEANUP_INTERVAL_MINUTES`. If `beat` is down,
expired sessions accumulate in the database and in object storage — data that was promised to
be deleted stays. Monitor that this task is actually running (its own log line:
`cleanup.swept`), not just that the container is up.

## Rolling back a bad deploy

The API is stateless; redeploy the previous image tag. Database migrations should be additive
and backward-compatible for at least one release (add columns/tables, don't drop them in the
same deploy that ships the code depending on their absence), so a rollback of the API image
alone is safe without a matching database rollback.

## What is NOT production-hardened yet

- No rate limiting is enforced at the app layer (config keys exist in `.env.example` but the
  middleware is not wired up) — put a rate limiter in front (nginx `limit_req`, or a proxy).
- No WAF / DDoS protection — standard reverse-proxy concerns, out of scope for this repo.
- The GPU worker's `ML_REPO_PATH` mount is read-only, which is correct, but there's no
  automated check that the mounted commit matches what `ML_COMMIT` claims — set both by hand
  and keep them consistent.
