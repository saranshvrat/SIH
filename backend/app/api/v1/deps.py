from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session as DbSession

from app.core.errors import NotFoundError
from app.db.models import Aoi, Job, Scene, Session
from app.db.session import get_db
from app.domains.session import get_or_create_session
from app.services.storage import StorageService, get_storage


def db_session() -> Iterator[DbSession]:
    yield from get_db()


def current_session(request: Request, response: Response,
                    db: DbSession = Depends(db_session)) -> Session:
    return get_or_create_session(request, response, db)


def storage() -> StorageService:
    return get_storage()


def owned_scene(scene_id: str, db: DbSession = Depends(db_session),
                sess: Session = Depends(current_session)) -> Scene:
    scene = db.get(Scene, scene_id)
    # Cross-session access is 404, never 403: job ids must not be enumerable into
    # other people's data (ADR-001).
    if not scene or scene.session_id != sess.id:
        raise NotFoundError(f"Scene {scene_id} was not found in this session.")
    return scene


def owned_job(job_id: str, db: DbSession = Depends(db_session),
              sess: Session = Depends(current_session)) -> Job:
    job = db.get(Job, job_id)
    if not job or job.session_id != sess.id:
        raise NotFoundError(
            f"Job {job_id} was not found in this session. PixelForge stores results only "
            f"for the browser session that created them.")
    return job


def owned_aoi(aoi_id: str, db: DbSession = Depends(db_session),
              sess: Session = Depends(current_session)) -> Aoi:
    aoi = db.get(Aoi, aoi_id)
    if not aoi or aoi.session_id != sess.id:
        raise NotFoundError(f"AOI {aoi_id} was not found in this session.")
    return aoi
