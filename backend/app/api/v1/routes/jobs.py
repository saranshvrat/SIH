from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session, owned_job
from app.api.v1.schemas.models_api import (
    JobCreate,
    JobCreateResponse,
    JobStatusResponse,
)
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import job_id as new_job_id
from app.db.models import Job, ModelRun, Scene, Session, in_hours
from app.ml.registry import available_model_keys, list_models
from app.services.storage import JOBS, get_storage

router = APIRouter(tags=["jobs"])


def _job_status(db: DbSession, job: Job) -> JobStatusResponse:
    runs = db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
    current = next(
        (s for s in (job.stages or []) if s.get("status") == "running"), None
    )
    error = None
    if job.error_code:
        error = {
            "code": job.error_code,
            "message": job.error_message,
            "stage": job.failed_stage,
            "retryable": job.error_code
            in {"SOURCE_UNAVAILABLE", "GPU_OUT_OF_MEMORY", "SERVICE_UNAVAILABLE"},
        }
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        mode=job.mode,
        progress=job.progress,
        current_stage=job.current_stage,
        stage_detail=(current or {}).get("detail"),
        stages=job.stages or [],
        runs=[
            {
                "run_id": r.id,
                "model_key": r.model_key,
                "status": r.status,
                "is_baseline": r.is_baseline,
                "output_gsd_m": r.output_gsd_m,
                "runtime_s": r.runtime_s,
                "error_code": r.error_code,
            }
            for r in runs
        ],
        terrain=(
            {
                "class": job.terrain_class,
                "distribution": job.terrain_distribution,
                "method": job.classification_method,
                "confidence": job.classification_confidence,
            }
            if job.terrain_class
            else None
        ),
        cloud_fraction_aoi=job.cloud_fraction_aoi,
        partial_success=job.partial_success,
        error=error,
        created_at=job.created_at.isoformat(),
        updated_at=job.finished_at.isoformat() if job.finished_at else None,
        duration_s=job.duration_s,
        expires_at=job.expires_at.isoformat(),
    )


@router.post("/jobs", response_model=JobCreateResponse, status_code=202)
def create_job(
    payload: JobCreate,
    db: DbSession = Depends(db_session),
    sess: Session = Depends(current_session),
) -> JobCreateResponse:
    if payload.idempotency_key:
        existing = (
            db.query(Job)
            .filter(
                Job.session_id == sess.id,
                Job.idempotency_key == payload.idempotency_key,
            )
            .first()
        )
        if existing:
            return JobCreateResponse(
                job_id=existing.id,
                status=existing.status,
                created_at=existing.created_at.isoformat(),
                poll_url=f"{settings.API_V1_PREFIX}/jobs/{existing.id}/status",
                already_existed=True,
            )

    scene = db.get(Scene, payload.scene_id)
    if not scene or scene.session_id != sess.id:
        raise NotFoundError(f"Scene {payload.scene_id} was not found in this session.")
    if scene.status == "needs_confirmation":
        raise ConflictError(
            "The band mapping for this file has not been confirmed. Confirm which band is "
            "which before processing.",
            {"scene_id": scene.id, "action": "confirm_bands"},
        )

    available = available_model_keys()
    models = payload.models or (
        [] if payload.auto_select_model else [settings.ML_DEFAULT_MODEL]
    )
    unavailable = [m for m in models if m not in available]
    if models and not [m for m in models if m in available]:
        catalogue = {m["key"]: m["unavailable_reason"] for m in list_models()}
        raise ValidationError(
            f"None of the requested models are available right now: {', '.join(unavailable)}.",
            {
                "requested": models,
                "available": available,
                "reasons": {m: catalogue.get(m) for m in unavailable},
            },
        )

    job = Job(
        id=new_job_id(),
        session_id=sess.id,
        scene_id=scene.id,
        aoi_id=payload.aoi_id,
        mode=payload.mode,
        status="QUEUED",
        current_stage="QUEUED",
        requested_models=[m for m in models if m in available],
        auto_select_model=payload.auto_select_model,
        options=payload.options.model_dump(),
        idempotency_key=payload.idempotency_key,
        expires_at=in_hours(settings.JOB_TTL_HOURS),
    )
    db.add(job)
    db.commit()

    from app.tasks.dispatch import dispatch_job

    task_id = dispatch_job(job.id)
    if task_id:
        job.celery_task_id = task_id
        db.add(job)
        db.commit()

    return JobCreateResponse(
        job_id=job.id,
        status=job.status,
        created_at=job.created_at.isoformat(),
        estimated_duration_s=None,
        poll_url=f"{settings.API_V1_PREFIX}/jobs/{job.id}/status",
    )


@router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
def job_status(
    job=Depends(owned_job), db: DbSession = Depends(db_session)
) -> JobStatusResponse:
    db.refresh(job)
    return _job_status(db, job)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job=Depends(owned_job), db: DbSession = Depends(db_session)
) -> JobStatusResponse:
    return _job_status(db, job)


@router.get("/jobs", response_model=list[JobStatusResponse])
def list_jobs(
    db: DbSession = Depends(db_session), sess: Session = Depends(current_session)
) -> list[JobStatusResponse]:
    """Session-scoped only. There is no cross-session history in PixelForge (ADR-001)."""
    rows = (
        db.query(Job)
        .filter(Job.session_id == sess.id)
        .order_by(Job.created_at.desc())
        .limit(50)
        .all()
    )
    return [_job_status(db, j) for j in rows]


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
def cancel_job(
    job=Depends(owned_job), db: DbSession = Depends(db_session)
) -> JobStatusResponse:
    if job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise ConflictError(f"This job has already finished ({job.status}).")
    from app.tasks.dispatch import cancel as cancel_task

    cancel_task(job)
    job.status = "CANCELLED"
    job.current_stage = "CANCELLED"
    db.add(job)
    db.commit()
    return _job_status(db, job)


@router.delete("/jobs/{job_id}", status_code=204, response_model=None)
def delete_job(job=Depends(owned_job), db: DbSession = Depends(db_session)) -> None:
    get_storage().delete_prefix(JOBS, f"jobs/{job.id}")
    db.delete(job)
    db.commit()
