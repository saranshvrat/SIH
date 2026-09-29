# ML Integration

How PixelForge talks to the ML team'"'"'s models, and what must be verified before trusting output.

## The seam

```
API / Celery  ──→  registry.py  ──→  <Model>Adapter  ──→  ML repository code
                       │                   │
                 model_catalog.yaml   verify_output_contract()
```

**Nothing above `app/ml/` imports PyTorch. Nothing below it knows about HTTP.**

All model-specific knowledge — import quirks, tensor layout, normalisation, whether the model
returns a georeferenced raster or a bare array — lives in **one adapter file**. If the ML team
changes something, exactly one file changes.

## The contract (frozen, v0.1)

`app/ml/contracts.py`. Changing any field is a breaking change: bump `CONTRACT_VERSION`,
announce it, update this document.

```python
@dataclass(frozen=True)
class InferenceRequest:
    input_path: Path          # a preprocessed GeoTIFF, bands already in model order
    output_dir: Path
    run_id: str
    input_bands: list[str]
    tile_px: int | None
    tile_overlap_px: int
    device: "cuda" | "cpu"
    seed: int | None
    num_samples: int          # >1 only if the model supports sampling
    want_uncertainty: bool
    progress_cb: Callable[[float, str], None] | None
```

```python
@dataclass
class InferenceResult:
    ok: bool
    sr_path: Path | None
    uncertainty_path: Path | None    # None means NOT PRODUCED — never fabricated
    output_gsd_m / output_bands / scale_factor / dtype / value_range
    runtime_s / peak_vram_mb / device_name / tiles_processed
    warnings / error_code / error_message
```

**`None` always means "not produced".** No adapter may ever substitute a plausible value.

## The output contract gate

`verify_output_contract()` runs after **every** inference and raises rather than returning a
result that breaches any of these:

| Check | Failure code |
|---|---|
| Output CRS equals input CRS | `CRS_MISMATCH` |
| Band count matches the adapter'"'"'s declaration | `BAND_COUNT_MISMATCH` |
| Measured scale factor matches the declaration | `SCALE_FACTOR_MISMATCH` |
| Footprint contained within the input (±1 output pixel) | `BOUNDS_DRIFT` |
| **Output GSD < 4.0 m** (the problem statement) | `GSD_ABOVE_PS_REQUIREMENT` |
| Output is not entirely NaN | `OUTPUT_ALL_NAN` |
| Output is not constant | `OUTPUT_CONSTANT` |

The <4 m requirement is a constant in code (`PS_MAX_GSD_M`), so the problem statement is
asserted by the test suite rather than living only in a slide.

Alongside it, `spectral_consistency_check()` downsamples the output back to the 10 m grid and
compares per-band means. It needs no ground truth, so it runs on every job and is the
platform'"'"'s own evidence for the "preserving spectral consistency" requirement.

## Model status

| Model | Status | Why |
|---|---|---|
| `bicubic` | **Working, tested** | Classical interpolation; no weights needed. CPU-only. |
| `wsx4_spatrad` | **Working, tested, VERIFIED** | Real ONNX model, CPU-only via onnxruntime. Preprocessing confirmed against the author's own eval notebook (ADR-007). |
| `wsx4_spatrad_mumbai_ft` | **Working, tested, VERIFIED** | Same as above; Mumbai-region fine-tune. |
| `sen2sr` | **Adapter written, UNVERIFIED** | Repository was unavailable at build time (ADR-004). |
| `ldsr_s2` | Not integrated | Nine scripts exist; weights are not committed. |
| `satlas` | Not integrated | Weights external; RGB-only ×8 breaks naive comparison. |
| `mamba_sr` | **Does not exist** | No implementation, scripts or weights. Listed as unavailable for transparency. |

Declared status can only be **downgraded** at runtime by `healthcheck()`, never upgraded. A
model whose weights are missing is reported `unavailable` with a reason, whatever the YAML says.

## Unblocking SEN2SR

1. Mount the repository and set `ML_REPO_PATH`.
2. Open `app/ml/adapters/sen2sr_adapter.py` and work through every `# ⚠️ VERIFY(§13.2 …)`
   marker with the ML team:

| Marker | Question that must be answered |
|---|---|
| `D2` | What is the real inference entrypoint, and what is its signature? |
| `A2` / `B9` | Exact band list **and order** the model expects. |
| `A4` / `A5` | Does the model normalise internally, or expect pre-scaled input? |
| `A9` | Batch dimension: `(B,C,H,W)` or `(C,H,W)`? |
| `B7` / `B8` | Measured scale factor and output GSD — from a real file, not config.json. |
| `B11` | Does it return a georeferenced raster or a bare array? |
| `C1` / `C2` | Measured peak VRAM, to set `max_input_px` honestly. |

3. Run the harness and read the verification block:

```bash
python scripts/smoke_adapter.py --model sen2sr --input patch.tif --out ./out
```

4. Open the output in QGIS next to the input. It must land in the right place on Earth.
5. Set `verified_against_repo: true` in `model_catalog.yaml` — **only then**.

### The normalisation decision

Normalisation is deliberately **not** applied in preprocessing. It belongs inside the model, so
it can never drift out of sync with the weights. If the ML team confirms the model expects
pre-scaled input, apply it in the adapter and nowhere else, and record the exact formula here.

## ONNX weight-file models (a second, simpler integration path)

Not every model needs the `ML_REPO_PATH` code-checkout treatment above. WSX4-SPATRAD ships as
a self-contained `.onnx` export + a YAML sidecar (bands, factor, margin) — there is no separate
inference code to integrate, because the ONNX graph fully describes the computation.

Set `ONNX_WEIGHTS_DIR` to a folder containing the `.onnx` file(s). The adapter
(`app/ml/adapters/wsx4_spatrad_adapter.py`) reads tensor input/output names directly from the
graph at load time — nothing is hardcoded.

**If you get another ONNX model from the same team**, check three things before trusting it,
exactly as was done for WSX4-SPATRAD:
1. **Input scaling** — raw DN, or normalised? Get this from their own eval script if one
   exists; guessing wrong here produces plausible-looking garbage with no error, which is the
   worst kind of silent failure.
2. **Margin/context padding protocol**, if any — some SR models need spatial context beyond
   the tile being upsampled and pad-then-crop around it.
3. **Band order** — confirm against their YAML/config, not assumed from ours.

## Adding a model

1. Write `app/ml/adapters/<name>_adapter.py` implementing the `SRModelAdapter` protocol.
2. Add an entry to `app/ml/model_catalog.yaml`.
3. Run the smoke harness.

**No frontend changes.** The interface renders itself from `GET /api/v1/models`.

## Uncertainty

The ladder from the master plan, and where the implementation actually stands:

| Level | Method | Status |
|---|---|---|
| U0 | Provenance and disclaimer layer | **Shipped** |
| U1 | Ensemble disagreement across models | **Shipped**, always `calibrated: false` |
| U2 | Diffusion sampling variance | Blocked — needs a sampling-capable model |
| U3 | MC dropout | ML team |
| U4 | Learned variance head | ML team |
| U5 | Empirical calibration against ground truth | Future work |

**Rule 12, absolute:** uncertainty is never derived from edge detection, a Laplacian, or local
variance. Those produce a map that looks convincing and means nothing. PixelForge computes it
only from genuine disagreement between independent outputs, and returns
`available: false, reason: NEEDS_MULTIPLE_RUNS` when only one model ran.
