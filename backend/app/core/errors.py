"""
One error envelope for the whole API (master plan §6.1).

The frontend renders by `code`, never by parsing `message`. Stack traces and file
paths never reach the client.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500
    retryable = False

    def __init__(self, message: str, details: dict[str, Any] | None = None,
                 code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    code, status_code = "NOT_FOUND", 404


class ValidationError(AppError):
    code, status_code = "VALIDATION_ERROR", 422


class ConflictError(AppError):
    code, status_code = "CONFLICT", 409


class RateLimitError(AppError):
    code, status_code, retryable = "RATE_LIMITED", 429, True


class ServiceUnavailableError(AppError):
    code, status_code, retryable = "SERVICE_UNAVAILABLE", 503, True


class PayloadTooLargeError(AppError):
    code, status_code = "FILE_TOO_LARGE", 413


# --- raster / upload -------------------------------------------------------
class RasterValidationError(ValidationError):
    code = "INVALID_RASTER"


class NotGeoreferencedError(RasterValidationError):
    code = "NOT_GEOREFERENCED"


class MissingCRSError(RasterValidationError):
    code = "MISSING_CRS"


class InvalidRasterGSDError(RasterValidationError):
    code = "INVALID_RASTER_GSD"


class InsufficientBandsError(RasterValidationError):
    code = "INSUFFICIENT_BANDS"


class BandMappingAmbiguousError(RasterValidationError):
    code = "BAND_MAPPING_AMBIGUOUS"


class CorruptRasterError(RasterValidationError):
    code = "CORRUPT_RASTER"


class UnsupportedFormatError(RasterValidationError):
    code = "UNSUPPORTED_FORMAT"


class AOITooLargeError(ValidationError):
    code = "AOI_TOO_LARGE"


class InvalidAOIError(ValidationError):
    code = "INVALID_AOI"


# --- providers -------------------------------------------------------------
class ProviderAuthError(AppError):
    code, status_code = "PROVIDER_AUTH_FAILED", 502


class SourceUnavailableError(AppError):
    code, status_code, retryable = "SOURCE_UNAVAILABLE", 502, True


class ProviderNotConfiguredError(AppError):
    code, status_code = "PROVIDER_NOT_CONFIGURED", 503


def _envelope(code: str, message: str, details: dict, request_id: str,
              retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "details": details,
                      "retryable": retryable, "request_id": request_id}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        rid = getattr(request.state, "request_id", "-")
        log.warning("app_error", code=exc.code, message=exc.message,
                    details=exc.details, request_id=rid)
        return JSONResponse(status_code=exc.status_code,
                            content=_envelope(exc.code, exc.message, exc.details, rid, exc.retryable))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", "-")
        return JSONResponse(status_code=422, content=_envelope(
            "VALIDATION_ERROR", "Request body or parameters are invalid.",
            {"fields": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()[:10]]}, rid))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "-")
        log.exception("unhandled_exception", request_id=rid, path=str(request.url.path))
        # Never leak the exception text or a stack trace to the client.
        return JSONResponse(status_code=500, content=_envelope(
            "INTERNAL_ERROR",
            "An unexpected error occurred. Quote the request id when reporting this.",
            {}, rid))
