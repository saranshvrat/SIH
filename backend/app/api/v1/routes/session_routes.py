"""
Session routes (ADR-001).

This is the visible face of the ephemeral data model: what PixelForge is holding for
you right now, and a button that destroys it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session
from app.core.config import settings
from app.db.models import Artifact, Job, Scene, Session
from app.domains.session import purge_session

router = APIRouter(tags=["session"])


@router.get("/session")
def get_session(db: DbSession = Depends(db_session),
                sess: Session = Depends(current_session)) -> dict[str, Any]:
    jobs = (db.query(Job).filter(Job.session_id == sess.id)
            .order_by(Job.created_at.desc()).limit(50).all())
    scenes = db.query(Scene).filter(Scene.session_id == sess.id).count()
    arts = db.query(Artifact).filter(Artifact.session_id == sess.id).all()
    return {
        "session_id": sess.id,
        "created_at": sess.created_at.isoformat(),
        "expires_at": sess.expires_at.isoformat(),
        "ttl_hours": settings.SESSION_TTL_HOURS,
        "jobs": [{"job_id": j.id, "status": j.status, "progress": j.progress,
                  "models": j.selected_models or j.requested_models,
                  "terrain": j.terrain_class,
                  "created_at": j.created_at.isoformat(),
                  "expires_at": j.expires_at.isoformat()} for j in jobs],
        "counts": {"jobs": len(jobs), "scenes": scenes, "files": len(arts)},
        "bytes_stored": sum(a.bytes for a in arts),
        "privacy": {
            "accounts": False,
            "what_is_stored": ("Job metadata, the imagery you selected or uploaded, and the "
                               "outputs PixelForge produced from it."),
            "where": "This server and its object storage.",
            "how_long": (f"Until this session expires ({settings.SESSION_TTL_HOURS} h) or "
                         f"{settings.JOB_TTL_HOURS} h after each job, whichever is sooner."),
            "how_to_delete_now": "DELETE /api/v1/session, or the 'Clear my data' button.",
        },
    }


@router.delete("/session")
def clear_session(db: DbSession = Depends(db_session),
                  sess: Session = Depends(current_session)) -> dict[str, Any]:
    """Hard-deletes every row and every stored object for this session. Irreversible."""
    result = purge_session(db, sess.id)
    return {"cleared": True, **result}
