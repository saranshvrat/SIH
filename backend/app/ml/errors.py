"""Typed ML-layer errors. Every one maps to a stable API error code."""
from __future__ import annotations


class MLError(Exception):
    code = "ML_ERROR"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class UnknownModelError(MLError):
    code = "UNKNOWN_MODEL"


class ModelUnavailableError(MLError):
    code = "MODEL_UNAVAILABLE"


class MLEntrypointNotFound(MLError):
    """The ML repo is mounted but the documented entrypoint could not be located.

    Raised with the exact list of paths/symbols tried, so the failure is actionable
    rather than a bare ImportError. See master plan §13.2 item D2.
    """

    code = "ML_ENTRYPOINT_NOT_FOUND"


class OutputContractViolation(MLError):
    """The adapter produced a file that fails the geospatial/scientific contract."""

    code = "OUTPUT_CONTRACT_VIOLATION"
