# Architecture

## Shape

A **modular monolith**: one FastAPI application, split by domain, with Celery for long work.
Microservices were considered and rejected — see ADR notes in the master plan §5. The cost of
distributed tracing, service discovery and cross-service transactions buys nothing at this
scale, and the seams that actually matter (ML, storage, catalogue) are already interfaces.

```
                         ┌──────────────┐
                         │   Browser    │
                         └──────┬───────┘
                                │ session cookie (HttpOnly)
                         ┌──────▼───────┐
                         │   FastAPI    │
                         └──┬────┬───┬──┘
              ┌─────────────┘    │   └─────────────┐
       ┌──────▼──────┐    ┌──────▼──────┐   ┌──────▼──────┐
       │ PostgreSQL  │    │    Redis    │   │  MinIO/S3   │
       │  (metadata) │    │   (broker)  │   │  (rasters)  │
       └─────────────┘    └──────┬──────┘   └─────────────┘
                                 │
                    ┌────────────┴────────────┐
             ┌──────▼──────┐          ┌───────▼───────┐
             │ cpu worker  │          │  gpu worker   │
             │ concurrency │          │  --pool=solo  │
             │      4      │          │ concurrency 1 │
             └─────────────┘          └───────┬───────┘
                                              │ read-only mount
                                      ┌───────▼───────┐
                                      │ ML repository │
                                      └───────────────┘
```

## Why two queues

Inference is the only step that touches VRAM. Running it on a dedicated queue with
`--pool=solo --concurrency 1` means **two jobs can never contend for the same GPU memory**.
That single constraint prevents most demo-day out-of-memory failures. Everything else —
downloads, preprocessing, previews, metrics, reports, cleanup — runs on the cpu queue at
concurrency 4.

Without a reachable broker the job runs in-process in a background thread. The *same*
`JobPipeline` class executes either way, so the code path tests exercise is the code path
production runs.

## Layers

| Layer | Responsibility | Must not |
|---|---|---|
| `api/v1/` | HTTP, validation, serialisation | contain business logic |
| `domains/` | Job orchestration, session lifecycle, analysis | know about HTTP |
| `services/` | Raster, preprocessing, metrics, uncertainty, geo, catalogue, LLM | know about the database |
| `ml/` | Model contract, registry, output verification | know about HTTP or the database |
| `db/` | SQLAlchemy models and session | contain logic |
| `core/` | Config, logging, errors, ids | import anything above it |

## The job pipeline

Ten stages, of which **only three can fail the job**:

```
QUEUED → DOWNLOADING* → PREPROCESSING* → SCENE_ANALYSIS → MODEL_SELECTION
       → INFERENCE* → QUALITY_ASSESSMENT → UNCERTAINTY → POST_PROCESSING
       → ANALYSIS → COMPLETED
                                                    * essential; all others degrade
```

If classification, metrics, uncertainty or analysis break, the user still receives a
super-resolved image with an honest "this step was skipped, here is why" note. This is the
design decision that makes the demo robust.

Every skipped stage carries a machine-readable `reason` and a human-readable `reason_text`, both
surfaced in the UI. **There are no silent gaps.**

## Progress

Stage weights are deliberately uneven — inference is 45% of the bar — so the indicator moves
believably during the long wait rather than sitting at 30% for two minutes.

Progress is delivered by **polling**, not SSE (ADR-005): with jobs measured in minutes, a 1.5 s
poll costs almost nothing and avoids proxy buffering, reconnection logic and a whole class of
"why did the bar freeze" bugs. TanStack Query stops polling automatically at a terminal state.

## Data flow: one job

1. Session cookie resolves to an anonymous `sessions` row.
2. `POST /jobs` validates the scene, checks model availability, writes a `jobs` row.
3. Dispatch → Celery gpu queue, or an in-process thread when no broker is present.
4. Source acquired (windowed `/vsicurl/` read, or fetched from storage for an upload).
5. Preprocessed **once** — AOI window, band ordering enforced, cloud fraction measured.
   Every model in the job consumes this identical file; otherwise comparison is invalid.
6. Scene characterised by spectral indices; model routing, with the reason recorded.
7. Inference per model → `verify_output_contract()` → COG with provenance tags → previews
   rendered with a stretch computed **once** on the low-resolution input.
8. Metrics, uncertainty (if ≥2 outputs), analyses.
9. Artifacts recorded with a TTL; the temporary directory is destroyed.

## Security

- **Path traversal is structurally impossible.** Every storage key is built by
  `services/keys.py` from validated ids; user input never contributes to a path. The local
  driver asserts containment again as defence in depth.
- **Uploads stream** with a running byte cap, never `file.read()`.
- **Files are validated by opening them**, not by extension.
- **Cross-session access returns 404, not 403**, so job ids cannot be probed for existence.
- **Errors never leak** stack traces, file paths or SQL. One envelope, a stable `code`, and a
  `request_id` that correlates to the structured logs.
- **No secrets in code.** All configuration is environment-driven and typed at startup.

## Frontend

- **Landing loads no MapLibre.** Heavy routes are lazy-loaded behind Suspense, so the hero —
  the first thing a judge sees — ships about 37 kB gzipped.
- **The hero runs no model.** Two precomputed images and a CSS radial mask driven by a single
  `requestAnimationFrame` lerp writing two CSS custom properties. No React re-render per frame.
- **The model picker is data-driven.** It renders whatever `GET /api/v1/models` returns,
  including unavailable models with their reasons, so adding a model needs no frontend change.
- **Comparison panes share one transform** and one server-side contrast stretch, so a visible
  difference is a difference in the model rather than in its presentation.
