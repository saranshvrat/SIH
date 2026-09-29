#!/usr/bin/env python3
"""
Generate the landing-page hero assets (ADR-006).

Produces a matched pair from ONE source image:
  hero-sharp.webp   the detailed version revealed under the lens
  hero-blocky.webp  the same scene degraded to look like 10 m input

    python scripts/make_hero_assets.py --input scene.tif --out frontend/public/hero

BEFORE THE DEMO: re-run this with a real PixelForge SR output as --input and a real
10 m Sentinel-2 crop as --lr. Then the hero shows your actual result rather than a
simulated degradation, and you can say so when a judge asks.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

TARGET = (1920, 1080)


def _load(path: Path) -> Image.Image:
    if path.suffix.lower() in (".tif", ".tiff", ".jp2"):
        import rasterio
        with rasterio.open(path) as src:
            idx = [4, 3, 2] if src.count >= 4 else [1, 2, 3][: src.count]
            arr = src.read(idx[:3]).astype("float64")
        rgb = np.zeros((arr.shape[1], arr.shape[2], 3), dtype=np.uint8)
        for i in range(min(3, arr.shape[0])):
            lo, hi = np.nanpercentile(arr[i], 2), np.nanpercentile(arr[i], 98)
            rgb[:, :, i] = (np.clip((arr[i] - lo) / max(hi - lo, 1e-9), 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(rgb, "RGB")
    return Image.open(path).convert("RGB")


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def degrade(img: Image.Image, factor: int = 8) -> Image.Image:
    """
    Honest degradation: average-pool down, then nearest-neighbour back up.

    Average pooling is what actually happens in a coarser sensor footprint, and
    nearest-neighbour restores the blocky pixel grid that makes "10 m" legible at a
    glance. This is a VISUAL ANALOGY for the hero, not a claim about the model — the
    page says so in the caption.
    """
    small = img.resize((max(1, img.width // factor), max(1, img.height // factor)), Image.BOX)
    return small.resize(img.size, Image.NEAREST)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True, help="sharp / SR image")
    ap.add_argument("--lr", type=Path, help="real low-res counterpart (preferred over --factor)")
    ap.add_argument("--out", type=Path, default=Path("frontend/public/hero"))
    ap.add_argument("--factor", type=int, default=8)
    ap.add_argument("--width", type=int, default=TARGET[0])
    ap.add_argument("--height", type=int, default=TARGET[1])
    args = ap.parse_args()

    size = (args.width, args.height)
    args.out.mkdir(parents=True, exist_ok=True)

    sharp = _cover(_load(args.input), size)
    blocky = _cover(_load(args.lr), size) if args.lr else degrade(sharp, args.factor)

    sharp.save(args.out / "hero-sharp.webp", "WEBP", quality=90, method=5)
    blocky.save(args.out / "hero-blocky.webp", "WEBP", quality=88, method=5)
    _cover(sharp, (960, 540)).save(args.out / "hero-sharp@sm.webp", "WEBP", quality=85)
    _cover(blocky, (960, 540)).save(args.out / "hero-blocky@sm.webp", "WEBP", quality=82)

    source = "a real low-resolution counterpart" if args.lr else \
             f"average-pooled {args.factor}x degradation of the sharp image"
    (args.out / "SOURCE.txt").write_text(
        f"hero-sharp.webp   <- {args.input}\n"
        f"hero-blocky.webp  <- {source}\n\n"
        "If no --lr was supplied, the blocky image is a visual analogy produced by\n"
        "downsampling, NOT a PixelForge model output. Re-run with --lr pointing at a\n"
        "genuine 10 m crop before the demo.\n")
    print(f"wrote {args.out}/hero-sharp.webp and hero-blocky.webp ({size[0]}x{size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
