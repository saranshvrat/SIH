# Applying the WSX4-SPATRAD integration

This folder mirrors your project's structure. Copy each file into the SAME path in your
`pixel-forge/` project, overwriting the existing one.

## Files to copy (source → destination in your project)

```
backend/app/ml/adapters/wsx4_spatrad_adapter.py   →  backend/app/ml/adapters/  (NEW file)
backend/app/ml/registry.py                        →  backend/app/ml/          (overwrite)
backend/app/ml/verify.py                          →  backend/app/ml/          (overwrite)
backend/app/ml/model_catalog.yaml                 →  backend/app/ml/          (overwrite)
backend/app/core/config.py                        →  backend/app/core/        (overwrite)
backend/requirements/base.txt                     →  backend/requirements/    (overwrite)
backend/tests/ml/test_contract.py                 →  backend/tests/ml/        (overwrite)
ml_weights/*.onnx, ml_weights/*.yaml              →  ml_weights/              (NEW folder,
                                                       create it at project root, same
                                                       level as backend/ and frontend/)
.env.example                                       →  project root             (overwrite,
                                                       OR just add the new ONNX_WEIGHTS_DIR
                                                       line to your existing .env by hand)
docs/DECISIONS.md, docs/ML-INTEGRATION.md         →  docs/                    (overwrite)
IMPLEMENTATION_STATUS.md                          →  project root             (overwrite)
```

## Steps

1. **Copy the files above** into your project, matching the paths.

2. **Add the new weights folder** if you haven't already — it should now exist at:
   ```
   pixel-forge/
   ├── ml_weights/
   │   ├── wsx4_spatrad.onnx
   │   ├── wsx4_spatrad.yaml
   │   ├── wsx4_spatrad_mumbai_ft.onnx
   │   └── wsx4_spatrad_mumbai_ft.yaml
   ```

3. **Add this line to your `.env`** (the actual file, not just `.env.example`):
   ```
   ONNX_WEIGHTS_DIR=./ml_weights
   ```

4. **Install the new Python dependency** (in your activated venv):
   ```bash
   pip install onnxruntime==1.19.2
   ```

5. **Restart the backend.** If uvicorn is running with `--reload`, saving the files should
   auto-restart it. If not, stop it (Ctrl+C) and run again:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

6. **Verify it worked** — two ways:

   Quick check via the API:
   ```bash
   curl http://localhost:8000/api/v1/models
   ```
   Look for `"wsx4_spatrad"` and `"wsx4_spatrad_mumbai_ft"` with `"status": "available"`.

   Or in the browser: refresh the Explore/Process page — both WSX4-SPATRAD models should
   now show up selectable (no longer greyed out) next to Bicubic.

   Full test suite (optional but recommended — this actually runs real inference against
   your weights):
   ```bash
   cd backend
   set ONNX_WEIGHTS_DIR=./../ml_weights        # Windows PowerShell: $env:ONNX_WEIGHTS_DIR="../ml_weights"
   python -m pytest tests -q
   ```
   You should see `48 passed` (was 45 before — the 3 new WSX4 tests now run against your
   real weight files instead of being skipped).

## What you now have working

- Two new real, working super-resolution models (not baselines) — genuinely tested against
  your actual `.onnx` files, not mocked.
- Both run on CPU via ONNX Runtime — no GPU, no Docker GPU worker setup needed for these.
- Selecting either one in the UI and running a job will now produce genuine AI-reconstructed
  output instead of just the bicubic interpolation baseline.
- Select BOTH wsx4_spatrad and wsx4_spatrad_mumbai_ft together in the model picker to also
  get a genuine uncertainty map (needs ≥2 models to compute disagreement).

## If something goes wrong

- `"status": "unavailable", "unavailable_reason": "WEIGHTS_NOT_FOUND"` → check the
  `ml_weights/` folder path and that `ONNX_WEIGHTS_DIR` in `.env` points to it correctly
  (relative to wherever you launch uvicorn from — usually `backend/`, so `./../ml_weights`
  or an absolute path is safer than `./ml_weights` if you're unsure).
- `"unavailable_reason": "ONNXRUNTIME_NOT_INSTALLED"` → run
  `pip install onnxruntime==1.19.2` in your activated venv.
- Import error on startup mentioning `wsx4_spatrad_adapter` → double check the adapter file
  landed at exactly `backend/app/ml/adapters/wsx4_spatrad_adapter.py`.
