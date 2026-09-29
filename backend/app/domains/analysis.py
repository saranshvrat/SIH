"""Geospatial analysis runner — computes on BOTH SR and LR, then compares (master plan §7J)."""
from __future__ import annotations

from pathlib import Path

import rasterio
from sqlalchemy.orm import Session as DbSession

from app.core.ids import analysis_id as new_analysis_id
from app.db.models import Analysis, Artifact, Job
from app.services import keys
from app.services.geo.indices import INDEX_DEFS, compare_lr_sr, compute_index
from app.services.raster.preview import render_colormap
from app.services.storage import JOBS, get_storage

SUPPORTED = list(INDEX_DEFS)


def run_analysis(db: DbSession, job: Job, run_id: str, kind: str, sr_path: Path,
                 lr_path: Path, band_names: list[str], tmp: Path) -> Analysis:
    if kind not in SUPPORTED:
        raise ValueError(f"Unsupported analysis {kind!r}. Available: {SUPPORTED}")
    storage = get_storage()
    aid = new_analysis_id()
    out = tmp / "analyses" / aid
    out.mkdir(parents=True, exist_ok=True)

    sr_res = compute_index(sr_path, out / "sr.tif", kind, band_names)
    lr_res = compute_index(lr_path, out / "lr.tif", kind, band_names)

    with rasterio.open(sr_path) as s, rasterio.open(lr_path) as l:
        px_sr, px_lr = float(s.res[0]) ** 2, float(l.res[0]) ** 2

    comparison = compare_lr_sr(lr_res, sr_res, px_lr, px_sr)

    analysis = Analysis(
        id=aid, job_id=job.id, run_id=run_id, type=kind, status="completed",
        params={"band_names": band_names},
        stats={"stats": sr_res["stats"], "classes": sr_res["classes"],
               "histogram": sr_res["histogram"], "formula": sr_res["formula"],
               "label": sr_res["label"], "pixel_count": sr_res["pixel_count"],
               "gsd_m": round(px_sr ** 0.5, 3)},
        lr_stats={"stats": lr_res["stats"], "classes": lr_res["classes"],
                  "gsd_m": round(px_lr ** 0.5, 3)},
        comparison_with_lr=comparison, computed_on="both")
    db.add(analysis)

    for which, res in (("sr", sr_res), ("lr", lr_res)):
        tif = out / f"{which}.tif"
        key = keys.analysis_cog(job.id, aid, which)
        size = storage.put_file(JOBS, key, tif)
        db.add(Artifact(session_id=job.session_id, owner_type="analysis", owner_id=aid,
                        kind=f"analysis_cog_{which}", format="COG", bucket=JOBS,
                        storage_key=key, bytes=size, expires_at=job.expires_at,
                        meta={"index": kind, "computed_on": which}))
        png = out / f"{which}.png"
        meta = render_colormap(tif, png, vmin=-1, vmax=1, colormap=res["colormap"])
        pkey = keys.analysis_preview(job.id, aid, which)
        psize = storage.put_file(JOBS, pkey, png)
        db.add(Artifact(session_id=job.session_id, owner_type="analysis", owner_id=aid,
                        kind=f"analysis_preview_{which}", format="PNG", bucket=JOBS,
                        storage_key=pkey, bytes=psize, expires_at=job.expires_at,
                        meta={**meta, "index": kind, "computed_on": which}))

    storage.put_json(JOBS, keys.analysis_stats(job.id, aid),
                     {"sr": analysis.stats, "lr": analysis.lr_stats, "comparison": comparison})
    db.commit()
    return analysis
