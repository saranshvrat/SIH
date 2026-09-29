#!/usr/bin/env python3
"""
Phase 3 acceptance harness (master plan §11 Phase 3).

Runs ONE model on ONE GeoTIFF with no FastAPI, no Celery, no database, and prints the
full contract verification. This is the script that proves the ML seam works.

    python scripts/smoke_adapter.py --model bicubic --input in.tif --out ./out
    python scripts/smoke_adapter.py --model sen2sr  --input in.tif --out ./out
    python scripts/smoke_adapter.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.errors import MLError                      # noqa: E402
from app.ml.registry import list_models, run_model      # noqa: E402
from app.ml.verify import spectral_consistency_check    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="PixelForge ML adapter smoke test")
    ap.add_argument("--model", default="bicubic")
    ap.add_argument("--input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("./out"))
    ap.add_argument("--device", default=None)
    ap.add_argument("--tile-px", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--list", action="store_true", help="list models and their live health")
    args = ap.parse_args()

    if args.list:
        for m in list_models():
            flag = "OK " if m["status"] == "available" else "-- "
            print(f"{flag}{m['key']:12} {m['status']:12} {m['unavailable_reason'] or ''}")
        return 0

    if not args.input:
        ap.error("--input is required unless --list is given")
    args.out.mkdir(parents=True, exist_ok=True)

    def progress(p: float, detail: str) -> None:
        bar = "#" * int(p * 28)
        print(f"\r  [{bar:<28}] {p*100:5.1f}%  {detail}", end="", flush=True)

    cfg = {"run_id": f"smoke_{args.model}", "seed": args.seed, "progress_cb": progress}
    if args.device:
        cfg["device"] = args.device
    if args.tile_px:
        cfg["tile_px"] = args.tile_px

    print(f"\nModel   : {args.model}\nInput   : {args.input}\n")
    try:
        result, integrity = run_model(args.model, args.input, args.out / "sr.tif", cfg)
    except MLError as exc:
        print(f"\n\nFAILED [{exc.code}] {exc.message}")
        if exc.details:
            print(json.dumps(exc.details, indent=2, default=str))
        return 2
    print()

    if not result.ok:
        print(f"\nFAILED [{result.error_code}] {result.error_message}")
        return 2

    print("\n--- InferenceResult ---")
    print(json.dumps(result.to_dict(), indent=2, default=str))
    print("\n--- verify_output_contract ---")
    print(json.dumps(integrity, indent=2))
    print("\n--- spectral consistency vs LR (no ground truth required) ---")
    print(json.dumps(spectral_consistency_check(args.input, result.sr_path), indent=2))

    ok = integrity["meets_ps_requirement"]
    print(f"\nPS 26142 <4 m requirement: {'PASS' if ok else 'FAIL'} "
          f"({integrity['output_gsd_m']} m)")
    print(f"Output: {result.sr_path}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
