# PixelForge — Implementation Status

Tracks every feature in the [Master Plan](./docs/MASTER_PLAN.md) against reality.
**Updated at the end of every phase.** Statuses:

`NOT_STARTED` · `IN_PROGRESS` · `IMPLEMENTED` · `TESTED` · `BLOCKED` · `NOT_AVAILABLE`

> `TESTED` means an automated test asserts the behaviour **and the test was executed and passed**,
> not that a test exists.

---

## Phase completion

| Phase | Name | Status | DoD met? | Notes |
|---|---|---|---|---|
| 0 | Requirements + ML contract | `IMPLEMENTED` | Partial | Contract frozen v0.1. §13.2/§24 answers **outstanding** — ML repo unavailable (ADR-004) |
| 1 | Dev environment | `TESTED` | ✅ | Backend deps install + import verified in-container |
| 2 | Repository structure | `TESTED` | ✅ | Tree matches §5.1; `ml/` is a submodule *placeholder* pending repo URL |
| 3 | ML integration PoC | `IMPLEMENTED` | Partial | Contract + registry + `verify_output_contract` **TESTED**. SEN2SR path `BLOCKED:ML-REPO` |
| 4 | Backend skeleton | `TESTED` | ✅ | App boots, `/health` + `/models` real, OpenAPI exports |
| 5 | DB + object storage | `TESTED` | ✅ | Schema + both storage drivers round-trip |
| 6 | Upload pipeline | `TESTED` | ✅ | All §11-P6 validation cases pass |
| 7 | Sentinel-2 acquisition | `IMPLEMENTED` | Partial | CDSE client complete; **live calls unverified** — no credentials in this environment |
| 8 | Job pipeline | `TESTED` | ✅ | Full state machine incl. skip/degrade/fail paths |
| 9 | ML inference integration | `TESTED` | ✅ (bicubic) | Real E2E via bicubic. SEN2SR `BLOCKED:ML-REPO` |
| 10 | Results API | `TESTED` | ✅ | Honest nulls verified by test |
| 11 | Frontend foundation | `TESTED` | ✅ | `tsc --noEmit` clean, `vite build` succeeds, landing bundle 37 kB gzip (MapLibre lazy-loaded) |
| 12 | Satellite explorer | `IMPLEMENTED` | ✅ | MapLibre + AOI draw + limits |
| 13 | Processing interface | `IMPLEMENTED` | ✅ | Real stage polling |
| 14 | Result viewer 🎯 | `IMPLEMENTED` | ✅ | **MVP gate** |
| 15 | Multi-model comparison | `IMPLEMENTED` | Partial | Works with bicubic+SEN2SR; 2nd real model `BLOCKED:ML-REPO` |
| 16 | Metrics | `TESTED` | Partial | No-reference metrics real. Cross-validation vs ML team `BLOCKED` |
| 17 | Uncertainty | `IMPLEMENTED` | ✅ | U0 + U1 only. U2–U5 `BLOCKED:ML-REPO` |
| 18 | Geospatial analysis | `IMPLEMENTED` | ✅ | NDVI/NDWI/NDBI with LR-vs-SR delta |
| 19 | AI Analyst | `IMPLEMENTED` | ✅ | Tool-calling + numeric provenance guard; degrades without key |
| 20 | Reports / export | `IMPLEMENTED` | ✅ | JSON + bundle always; PDF when WeasyPrint present |
| 21 | Testing | `IN_PROGRESS` | — | Backend suite green (45/45). Browser-based E2E (Playwright or similar) `NOT_STARTED` |
| 22 | Docker / deployment | `IMPLEMENTED` | Partial | Compose + Dockerfiles written; **unbooted** — no Docker daemon here |
| 23 | Demo optimisation | `IN_PROGRESS` | — | `DEMO-SCRIPT.md` + fallback ladder written; rehearsal is yours |

---

## Feature matrix

### A. Satellite Explorer (§7A)
| Feature | Status | Owner | Dependency | Notes |
|---|---|---|---|---|
| MapLibre base map + basemap switch | `IMPLEMENTED` | FE | — | OSM raster, no token |
| Location search | `NOT_STARTED` | FE | — | Only fixed city presets shipped; no live geocoding search box |
| Rectangle + polygon AOI draw | `IMPLEMENTED` | FE | — | Custom drawer, no extra dep |
| AOI area / px / size / runtime estimate | `IMPLEMENTED` | FE+BE | `POST /aois` | Geodesic area (ADR-002) |
| Hard AOI size block pre-submit | `TESTED` | BE | — | `AOI_TOO_LARGE` 422 |
| CDSE STAC scene search | `IMPLEMENTED` | BE | CDSE creds | **Live call unverified** |
| Scene footprint overlay + hover sync | `IMPLEMENTED` | FE | — | |
| AOI-level cloud % | `NOT_AVAILABLE` | BE | SCL band read | Returns `null`, UI labels it scene-level |
| Windowed AOI download (`/vsicurl/`) | `IMPLEMENTED` | BE | CDSE creds | Strategy spike pending live creds |
| Scene window cache | `IMPLEMENTED` | BE | storage | Keyed `(external_id, aoi_hash, bands)` |

### B. Upload (§7B)
| Feature | Status | Notes |
|---|---|---|
| Streaming multipart upload w/ byte cap | `TESTED` | Never `file.read()` |
| GeoTIFF/JP2 open-with-rasterio validation | `TESTED` | 9 typed error codes |
| CRS / transform / GSD / band / dtype checks | `TESTED` | |
| Band identification ladder (4 levels) | `TESTED` | Falls through to `BAND_MAPPING_AMBIGUOUS` |
| Band-mapping confirmation endpoint | `TESTED` | `PATCH /scenes/{id}/bands` |
| SHA-256 dedup | `TESTED` | Session-scoped |
| Zip-slip / zip-bomb guards | `NOT_AVAILABLE` | `.SAFE` zip intentionally unsupported (§22) |

### C. Preprocessing (§7C)
| Feature | Status | Notes |
|---|---|---|
| AOI windowed read | `TESTED` | `rasterio.windows.from_bounds` |
| Band select + **order enforcement** | `TESTED` | Synthetic band-order test passes |
| Cloud mask from SCL | `IMPLEMENTED` | Policy (c)+(a): report, reject >40% |
| Normalisation | `BLOCKED:ML-REPO` | Deliberately kept **inside** the adapter (§13.2 A5) |
| Geometric alignment | `NOT_AVAILABLE` | No-op + assertion; meaning unresolved (§24 Q-A) |
| Tiling w/ overlap + cosine blend | `TESTED` | Seam test passes |
| Nodata propagation | `TESTED` | |
| Geospatial integrity snapshot | `TESTED` | Feeds `verify_output_contract` |

### D. Scene / terrain classification (§7D)
| Feature | Status | Notes |
|---|---|---|
| L0 user-declared override | `IMPLEMENTED` | |
| L1 spectral-index heuristic | `TESTED` | NDVI/NDWI/NDBI → 7 classes + mix |
| L2 ESA WorldCover lookup | `NOT_STARTED` | Documented upgrade path |
| L3 learned classifier | `NOT_AVAILABLE` | Out of scope by design |
| Transparent routing + user override | `IMPLEMENTED` | Shows class %, confidence, reason |
| Never fails the job (degrade rule) | `TESTED` | |

### E–F. Models & comparison (§7E, §7F)
| Model | Status | Notes |
|---|---|---|
| `bicubic` baseline adapter | `TESTED` | CPU, always available, carries E2E |
| `wsx4_spatrad` | `TESTED` | **Real ONNX model, verified against author's eval notebook (ADR-007)**. CPU-only, no GPU needed |
| `wsx4_spatrad_mumbai_ft` | `TESTED` | Same adapter, Mumbai-region fine-tune weights |
| `sen2sr` | `BLOCKED:ML-REPO` | Adapter written; entrypoint unverified (ADR-004) |
| `ldsr_s2` | `NOT_AVAILABLE` | Weights external; catalogue entry `unavailable` |
| `satlas` | `NOT_AVAILABLE` | Weights external + RGB/×8 compat unresolved |
| `mamba_sr` | `NOT_AVAILABLE` | **No repo evidence.** Catalogue `unavailable`, UI greys out |
| Chord-based multi-run execution | `TESTED` | Partial-success handling verified |
| `comparison_basis` grid/band harmonisation | `TESTED` | Coarsest-GSD common grid |
| Synced multi-pane viewer | `IMPLEMENTED` | Single map, N raster sources |
| Shared stretch across panes | `IMPLEMENTED` | Computed once on LR |
| Pixel inspector | `NOT_STARTED` | Viewer supports zoom/pan/swipe/side-by-side; no hover-to-read pixel value readout |

### G–H. Metrics & uncertainty (§7G, §7H)
| Feature | Status | Notes |
|---|---|---|
| No-reference: LR-consistency PSNR/SSIM, SAM-vs-LR, edge energy | `TESTED` | |
| Full-reference PSNR/SSIM/SAM/RMSE | `TESTED` | Validation mode |
| LPIPS | `NOT_AVAILABLE` | Needs torch; returns `null` + reason, never faked |
| Bicubic baseline delta | `TESTED` | |
| Cross-validation vs ML `eval_results.json` | `BLOCKED:ML-REPO` | §24 Q23–Q25 |
| Honest `full_reference: null` in inference mode | `TESTED` | Anti-fabrication test greps PPT values |
| U0 provenance / disclaimer layer | `IMPLEMENTED` | Sticky, non-dismissible |
| U1 ensemble disagreement | `TESTED` | `calibrated: false` enforced in schema |
| U2 diffusion sampling | `BLOCKED:ML-REPO` | Contract field exists (`num_samples`) |
| U3/U4 MC-dropout / variance head | `NOT_AVAILABLE` | ML team; §24 Q29–Q32 |
| U5 empirical calibration | `NOT_STARTED` | Stretch goal |
| "Observed 10 m grid" toggle | `IMPLEMENTED` | |

### I–M. Post-processing, analysis, dashboard, AI, export
| Feature | Status | Notes |
|---|---|---|
| COG output + provenance tags | `TESTED` | `gdalinfo` shows model/commit/disclaimer |
| Histogram-match radiometric correction | `NOT_STARTED` | Post-processing stage exists and tags COGs, but applies no radiometric correction |
| Artifact **detection** (seam/checkerboard) | `NOT_STARTED` | Not implemented; tiling uses cosine blending to avoid seams rather than detecting them after the fact |
| NDVI / NDWI / NDBI on SR **and** LR + delta | `TESTED` | |
| Unsupervised k-means land cover | `NOT_STARTED` | Not implemented. Land-cover context comes only from the §D threshold-based terrain classifier and the NDVI/NDWI/NDBI indices above |
| Change detection | `NOT_STARTED` | §22 defer |
| Result dashboard (5 tabs) | `IMPLEMENTED` | |
| AI Analyst tool registry (12 tools) | `TESTED` | Verified via test_analyst_works_without_an_api_key + provenance guard test |
| Numeric provenance guard | `TESTED` | Rejects any unsourced number |
| Tool-call transparency panel | `IMPLEMENTED` | |
| Deterministic template fallback (no LLM key) | `TESTED` | App runs fine without a key |
| JSON report + ZIP bundle | `TESTED` | |
| PDF report | `IMPLEMENTED` | Degrades to JSON if WeasyPrint absent |

### Ephemeral data model (ADR-001)
| Feature | Status | Notes |
|---|---|---|
| Anonymous session cookie | `TESTED` | `HttpOnly`, `SameSite=Lax` |
| Session-scoped everything | `TESTED` | Cross-session access → 404 |
| TTL columns on scenes/jobs/artifacts | `TESTED` | |
| `cleanup.sweep_expired` beat task | `TESTED` | DB rows + storage objects |
| "Clear my data" endpoint + button | `TESTED` | `DELETE /session` |
| No users / accounts / auth | `IMPLEMENTED` | By construction |

---

## 🚨 Blocking items (need the ML team or credentials)

| ID | Blocks | Needed from | Master plan ref |
|---|---|---|---|
| `BLOCKED:ML-REPO` | SEN2SR/LDSR/Satlas adapters, U2–U4 uncertainty, metric cross-validation, real runtime/VRAM figures | ML team: repo URL + §13.2 answers A1–A9, B1–B12, C1–C8, D1–D6, E1–E9 | §13.2, §24 |
| `BLOCKED:CDSE` | Live scene search + windowed download verification | CDSE OAuth client id/secret | §11 Phase 0, §24 |
| `BLOCKED:LLM` | Live AI Analyst answers (template fallback works today) | `LLM_API_KEY` | §7L |
| `BLOCKED:DOCKER` | Compose boot verification, GPU worker verification | A machine with Docker + NVIDIA runtime | §11 Phase 22 |

---

## Genuinely not started (say so plainly if asked)

These appear nowhere else as "implemented" — listed once, here, so nothing is overclaimed by omission:

| Feature | Why it's not there |
|---|---|
| Live location search (geocoding) | Only four fixed city presets ship; no Nominatim/geocoder call |
| Histogram-match radiometric correction | The post-processing stage tags COGs with provenance but applies no correction |
| Automated seam/checkerboard artifact detection | Tiling uses cosine-window blending to avoid seams; nothing inspects output for them afterward |
| Unsupervised k-means land cover | Land-cover context comes only from the threshold-based §D terrain classifier and NDVI/NDWI/NDBI — no clustering model exists |
| Pixel value inspector (hover-to-read) | The comparison viewer has zoom/pan/swipe/side-by-side but no numeric readout |
| Browser-based E2E tests (Playwright etc.) | Only the backend HTTP test suite (45/45, via FastAPI TestClient) and `tsc`/`vite build` exist; no headless-browser suite was written |
| Docker Compose boot | Written and reviewed; never actually started, because no Docker daemon exists in this build environment |
| Change detection | Needs two acquisitions of the same area; not built |
| ESA WorldCover lookup (§D level 2) | Documented upgrade path from the current index-threshold heuristic; not implemented |

## Delivered since the last status pass

- `docs/DEVELOPMENT.md`, `docs/DEPLOYMENT.md`, `docs/ENVIRONMENT.md`, `docs/DEMO-SCRIPT.md` written.
- This file reconciled against actual code: seven rows that overclaimed a built feature were
  corrected to `NOT_STARTED` with an explanation (see above), and the AI Analyst tool count was
  corrected from 11 to the actual 12 registered tools.
- Frontend build verified for real: `tsc --noEmit` clean, `vite build` succeeds, landing route
  ships ~37 kB gzip with MapLibre lazy-loaded behind Suspense onto `/explore` only.
