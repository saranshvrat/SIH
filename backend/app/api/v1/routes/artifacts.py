from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session
from app.core.config import settings
from app.core.errors import NotFoundError
from app.db.models import Artifact, Session
from app.services.storage import LocalDriver, get_storage

router = APIRouter(tags=["artifacts"])

MEDIA = {"COG": "image/tiff", "GTiff": "image/tiff", "WEBP": "image/webp",
         "PNG": "image/png", "JSON": "application/json", "ZIP": "application/zip",
         "PDF": "application/pdf"}


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, db: DbSession = Depends(db_session),
             sess: Session = Depends(current_session)):
    art = db.get(Artifact, artifact_id)
    if not art or art.session_id != sess.id:
        raise NotFoundError("This file was not found in your session, or it has expired.")
    storage = get_storage()
    filename = f"{art.kind}_{art.id}.{art.format.lower()}"
    url = storage.presign_get(art.bucket, art.storage_key, settings.S3_PRESIGN_TTL, filename)
    if url.startswith("http"):
        return RedirectResponse(url, status_code=307)
    return RedirectResponse(url, status_code=307)


@router.get("/storage/download", include_in_schema=False)
def local_download(bucket: str = Query(...), key: str = Query(...),
                   exp: int = Query(...), sig: str = Query(...),
                   filename: str | None = Query(None)):
    """Serves HMAC-signed local-driver URLs (ADR-003). The S3 driver never reaches here."""
    if not LocalDriver.verify_signature(bucket, key, exp, sig):
        raise NotFoundError("This download link is invalid or has expired.")
    storage = get_storage()
    tmp = Path(tempfile.mkdtemp()) / (filename or Path(key).name)
    storage.get_to_path(bucket, key, tmp)
    suffix = tmp.suffix.lstrip(".").upper()
    return FileResponse(tmp, media_type=MEDIA.get(suffix, "application/octet-stream"),
                        filename=filename or tmp.name)
