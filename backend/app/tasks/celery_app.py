"""
Celery application (master plan §10.2).

TWO QUEUES, deliberately:
  cpu — downloads, preprocessing, previews, metrics, reports, cleanup. Concurrency 4.
  gpu — inference ONLY. Concurrency 1, --pool=solo, so two jobs can never contend for
        the same VRAM. This single constraint prevents most demo-day OOM failures.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "pixelforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.pipeline_tasks", "app.tasks.cleanup", "app.tasks.reports"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,          # long tasks: never hoard
    task_soft_time_limit=settings.JOB_SOFT_TIME_LIMIT,
    task_time_limit=settings.JOB_TIME_LIMIT,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=False,
    result_expires=settings.JOB_TTL_HOURS * 3600,
    task_routes={
        "pixelforge.run_job": {"queue": "gpu"},
        "pixelforge.build_report": {"queue": "cpu"},
        "pixelforge.sweep_expired": {"queue": "cpu"},
    },
    beat_schedule={
        "sweep-expired-sessions": {
            "task": "pixelforge.sweep_expired",
            "schedule": crontab(minute=f"*/{settings.CLEANUP_INTERVAL_MINUTES}"),
        },
    },
)
