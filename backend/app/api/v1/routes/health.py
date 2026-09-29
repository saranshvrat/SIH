from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import db_session
from app.api.v1.schemas.common import HealthResponse
from app.core.config import APP_VERSION, settings
from app.ml.contracts import CONTRACT_VERSION
from app.ml.registry import available_model_keys
from app.services.storage import get_storage

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession = Depends(db_session)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        storage_health = get_storage().healthcheck()
    except Exception as exc:  # noqa: BLE001
        storage_health = {"ok": False, "error": str(exc)}

    redis_ok, gpu_workers = False, 0
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        r.ping()
        redis_ok = True
        from app.tasks.celery_app import celery_app
        stats = celery_app.control.inspect(timeout=0.5).active_queues() or {}
        gpu_workers = sum(1 for qs in stats.values() if any(q["name"] == "gpu" for q in qs))
    except Exception:
        pass

    models = available_model_keys()
    ok = db_ok and storage_health.get("ok") and bool(models)
    return HealthResponse(
        status="ok" if ok else "degraded", version=APP_VERSION,
        environment=settings.ENVIRONMENT, database=db_ok, storage=storage_health,
        redis=redis_ok, gpu_workers_online=gpu_workers, models_available=models,
        # Sentinel-2 search is backed by Microsoft Planetary Computer's public STAC
        # catalogue — no account or credentials needed, so it's always on.
        features={"catalog": True, "llm": settings.llm_enabled,
                  "async_workers": redis_ok},
        contract_version=CONTRACT_VERSION)
