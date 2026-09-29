"""
The one place that decides HOW a job runs.

With Redis up, jobs go to the Celery gpu queue. Without it — a laptop with only
Python, or the test suite — the pipeline runs INLINE in a background thread so the
application still works end to end. The stage logic is identical either way, because
both paths call the same JobPipeline.
"""
from __future__ import annotations

import threading

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Job
from app.db.session import SessionLocal

log = get_logger(__name__)


def _broker_available() -> bool:
    try:
        import redis
        redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=0.5).ping()
        return True
    except Exception:
        return False


def _run_inline(job_id: str) -> None:
    from app.domains.pipeline import JobPipeline
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job and job.status != "CANCELLED":
            JobPipeline(db, job).run()
    except Exception:  # noqa: BLE001
        log.exception("inline_job.crashed", job_id=job_id)
    finally:
        db.close()


def dispatch_job(job_id: str) -> str | None:
    """Returns a Celery task id, or None when the job was run inline."""
    if _broker_available():
        from app.tasks.pipeline_tasks import run_job
        return run_job.apply_async(args=[job_id], queue="gpu").id

    log.info("dispatch.inline", job_id=job_id,
             reason="no Celery broker reachable; running in-process")
    if settings.CELERY_TASK_ALWAYS_EAGER:
        _run_inline(job_id)                      # synchronous — used by tests
    else:
        threading.Thread(target=_run_inline, args=(job_id,), daemon=True).start()
    return None


def cancel(job: Job) -> None:
    if not job.celery_task_id:
        return
    try:
        from app.tasks.celery_app import celery_app
        celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
    except Exception:  # noqa: BLE001
        log.warning("cancel.revoke_failed", job_id=job.id)
