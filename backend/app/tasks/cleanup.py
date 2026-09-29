"""
TTL sweeper (ADR-001).

This task is what makes "ephemeral" a property of the system rather than a promise
in the README. It runs every CLEANUP_INTERVAL_MINUTES and hard-deletes expired
sessions along with every database row and stored object that hangs off them.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.domains.session import sweep_expired as _sweep
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="pixelforge.sweep_expired")
def sweep_expired() -> dict:
    db = SessionLocal()
    try:
        result = _sweep(db)
        if result["sessions"]:
            log.info("cleanup.swept", **result)
        return result
    finally:
        db.close()
