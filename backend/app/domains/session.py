"""Anonymous session handling — the ONLY notion of identity in PixelForge (ADR-001)."""
from __future__ import annotations

import hashlib

from fastapi import Request, Response
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.ids import session_id as new_session_id
from app.db.models import Artifact, ChatSession, Job, Scene, Session, in_hours, utcnow
from app.services.storage import JOBS, SCENES, get_storage


def get_or_create_session(request: Request, response: Response, db: DbSession) -> Session:
    sid = request.cookies.get(settings.SESSION_COOKIE_NAME)
    sess = db.get(Session, sid) if sid else None

    if sess and sess.expires_at.replace(tzinfo=sess.expires_at.tzinfo or None) and \
            sess.expires_at.timestamp() < utcnow().timestamp():
        purge_session(db, sess.id)
        sess = None

    if sess is None:
        ua = request.headers.get("user-agent", "")
        sess = Session(id=new_session_id(), expires_at=in_hours(settings.SESSION_TTL_HOURS),
                       user_agent_hash=hashlib.sha256(ua.encode()).hexdigest()[:32])
        db.add(sess)
        db.commit()

    sess.last_seen_at = utcnow()
    db.add(sess)
    db.commit()

    response.set_cookie(
        settings.SESSION_COOKIE_NAME, sess.id, max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True, samesite="lax", secure=settings.ENVIRONMENT == "production", path="/")
    return sess


def purge_session(db: DbSession, sid: str) -> dict:
    """Hard-delete every row AND every stored object for a session. Powers 'Clear my data'."""
    storage = get_storage()
    objects = 0
    for art in db.query(Artifact).filter(Artifact.session_id == sid).all():
        try:
            storage.delete(art.bucket, art.storage_key)
            objects += 1
        except Exception:
            pass
    for job in db.query(Job).filter(Job.session_id == sid).all():
        try:
            objects += storage.delete_prefix(JOBS, f"jobs/{job.id}")
        except Exception:
            pass
    for scene in db.query(Scene).filter(Scene.session_id == sid).all():
        try:
            objects += storage.delete_prefix(SCENES, f"scenes/{scene.id}")
        except Exception:
            pass

    sess = db.get(Session, sid)
    rows = 0
    if sess:
        rows = (db.query(Job).filter(Job.session_id == sid).count()
                + db.query(Scene).filter(Scene.session_id == sid).count())
        db.delete(sess)            # ON DELETE CASCADE removes everything downstream
        db.commit()
    return {"session_id": sid, "rows_deleted": rows, "objects_deleted": objects}


def sweep_expired(db: DbSession) -> dict:
    """Beat task body: delete every session past its TTL."""
    now = utcnow()
    expired = [s.id for s in db.query(Session).filter(Session.expires_at < now).all()]
    total = {"sessions": 0, "rows": 0, "objects": 0}
    for sid in expired:
        r = purge_session(db, sid)
        total["sessions"] += 1
        total["rows"] += r["rows_deleted"]
        total["objects"] += r["objects_deleted"]
    return total
