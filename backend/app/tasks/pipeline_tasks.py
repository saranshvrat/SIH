"""Celery task wrappers. All real logic lives in app.domains.pipeline."""
from __future__ import annotations

from app.core.logging import bind, clear, get_logger
from app.db.models import Job
from app.db.session import SessionLocal
from app.domains.pipeline import JobPipeline
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="pixelforge.run_job", bind=True, max_retries=1)
def run_job(self, job_id: str) -> dict:
    db = SessionLocal()
    bind(job_id=job_id, task_id=self.request.id)
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.warning("job.missing", job_id=job_id)
            return {"job_id": job_id, "status": "MISSING"}
        if job.status == "CANCELLED":
            return {"job_id": job_id, "status": "CANCELLED"}
        JobPipeline(db, job).run()
        return {"job_id": job.id, "status": job.status, "progress": job.progress}
    finally:
        clear()
        db.close()
