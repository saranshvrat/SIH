from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import db_session, owned_job
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Analysis, ModelRun
from app.domains.analysis import SUPPORTED, run_analysis
from app.services import keys
from app.services.geo.indices import INDEX_DEFS
from app.services.storage import JOBS, get_storage

router = APIRouter(tags=["analysis"])


class AnalysisCreate(BaseModel):
    type: str
    run_id: str | None = None


@router.get("/analyses/available")
def available_analyses() -> dict[str, Any]:
    return {"analyses": [
        {"type": k, "label": v["label"], "formula": v["formula"],
         "requires_bands": list(v["bands"]), "status": "available",
         "computed_on": "both",
         "note": ("Computed on both the super-resolved and the original 10 m imagery so "
                  "the difference is visible and checkable.")}
        for k, v in INDEX_DEFS.items()
    ] + [
        {"type": "change_detection", "label": "Change detection", "status": "unavailable",
         "reason": "NOT_IMPLEMENTED",
         "note": "Requires two acquisition dates over the same area. Planned, not built."},
        {"type": "land_cover_classification", "label": "Supervised land cover",
         "status": "unavailable", "reason": "NOT_IMPLEMENTED",
         "note": "PixelForge provides index-based land cover only; no trained classifier."},
    ]}


@router.post("/jobs/{job_id}/analyses", status_code=201)
def create_analysis(payload: AnalysisCreate, job=Depends(owned_job),
                    db: DbSession = Depends(db_session)) -> dict[str, Any]:
    if payload.type not in SUPPORTED:
        raise ValidationError(
            f"Analysis {payload.type!r} is not available.",
            {"requested": payload.type, "available": SUPPORTED})
    if job.status != "COMPLETED":
        raise ConflictError(f"This job is {job.status}; analysis needs a completed job.")

    run = (db.get(ModelRun, payload.run_id) if payload.run_id else
           db.query(ModelRun).filter(ModelRun.job_id == job.id,
                                     ModelRun.status == "completed").first())
    if not run or run.job_id != job.id:
        raise NotFoundError("No completed model run was found for this job.")

    storage = get_storage()
    tmp = Path(tempfile.mkdtemp(prefix="pf_an_"))
    sr = storage.get_to_path(JOBS, keys.sr_cog(job.id, run.id), tmp / "sr.tif")
    lr = storage.get_to_path(JOBS, keys.lr_tif(job.id), tmp / "lr.tif")
    a = run_analysis(db, job, run.id, payload.type, sr_path=sr, lr_path=lr,
                     band_names=run.output_bands or ["B02", "B03", "B04", "B08"], tmp=tmp)
    return {"analysis_id": a.id, "type": a.type, "status": a.status,
            "sr": a.stats, "lr": a.lr_stats, "comparison_with_lr": a.comparison_with_lr}


@router.get("/jobs/{job_id}/analyses")
def list_analyses(job=Depends(owned_job), db: DbSession = Depends(db_session)) -> dict[str, Any]:
    rows = db.query(Analysis).filter(Analysis.job_id == job.id).all()
    return {"analyses": [{"analysis_id": a.id, "type": a.type, "status": a.status,
                          "run_id": a.run_id, "sr": a.stats, "lr": a.lr_stats,
                          "comparison_with_lr": a.comparison_with_lr} for a in rows]}
