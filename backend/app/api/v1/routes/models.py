from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.schemas.models_api import ModelListResponse
from app.core.config import settings
from app.ml.contracts import CONTRACT_VERSION
from app.ml.registry import list_models

router = APIRouter(tags=["models"])


@router.get("/models", response_model=ModelListResponse)
def get_models() -> ModelListResponse:
    """
    The contract that keeps the frontend model-agnostic.

    The interface renders itself from this response. Adding a model is a registry
    entry plus an adapter file; zero frontend changes.
    """
    return ModelListResponse(models=list_models(), default_model=settings.ML_DEFAULT_MODEL,
                             contract_version=CONTRACT_VERSION)
