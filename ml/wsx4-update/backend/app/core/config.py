"""All configuration in one typed place. Fails fast at startup on bad config."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore", case_sensitive=False)

    # --- core ---
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:4173"
    SECRET_KEY: str = "dev-only-change-me"

    # --- database ---
    DATABASE_URL: str = "sqlite:///./pixelforge.db"

    # --- redis / celery ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_ALWAYS_EAGER: bool = False

    # --- storage (ADR-003: pluggable driver) ---
    STORAGE_DRIVER: Literal["s3", "local"] = "local"
    STORAGE_LOCAL_ROOT: str = "./data/storage"
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_SCENES: str = "pixelforge-scenes"
    S3_BUCKET_JOBS: str = "pixelforge-jobs"
    S3_BUCKET_REPORTS: str = "pixelforge-reports"
    S3_PRESIGN_TTL: int = 900

    # --- ephemeral session model (ADR-001) ---
    SESSION_COOKIE_NAME: str = "pf_session"
    SESSION_TTL_HOURS: int = 12
    JOB_TTL_HOURS: int = 24
    SCENE_CACHE_TTL_HOURS: int = 72
    CLEANUP_INTERVAL_MINUTES: int = 15

    # --- ML ---
    ML_REPO_PATH: str = "/ml"
    ML_WEIGHTS_PATH: str = "/weights"
    ML_COMMIT: str = "unknown"
    ML_DEVICE: Literal["cuda", "cpu"] = "cuda"
    ML_KEEP_MODELS_RESIDENT: bool = True
    ML_DEFAULT_MODEL: str = "bicubic"

    # ONNX weight-file models (e.g. WSX4-SPATRAD). These are self-contained
    # .onnx exports + a YAML sidecar — no separate code repository needed,
    # unlike the PyTorch-source ML_REPO_PATH models above. Point this at the
    # folder containing your .onnx files.
    ONNX_WEIGHTS_DIR: str = "./ml_weights"

    # --- Copernicus Data Space Ecosystem ---
    CDSE_CLIENT_ID: str = ""
    CDSE_CLIENT_SECRET: str = ""
    CDSE_TOKEN_URL: str = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
                           "protocol/openid-connect/token")
    CDSE_STAC_URL: str = "https://catalogue.dataspace.copernicus.eu/stac"
    CDSE_S3_ENDPOINT: str = "https://eodata.dataspace.copernicus.eu"

    # --- LLM (optional; app must run without it) ---
    LLM_PROVIDER: Literal["anthropic", "openai", "none"] = "none"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "claude-sonnet-4-6"
    LLM_BASE_URL: str = ""
    LLM_MAX_TOKENS: int = 1024
    LLM_TEMPERATURE: float = 0.1

    # --- limits (master plan §17) ---
    MAX_UPLOAD_MB: int = 200
    MAX_AOI_KM2: float = 25.0
    MAX_INPUT_PX: int = 512
    JOB_SOFT_TIME_LIMIT: int = 1500
    JOB_TIME_LIMIT: int = 1800
    RATE_LIMIT_JOBS_PER_MIN: int = 5
    RATE_LIMIT_UPLOADS_PER_MIN: int = 10
    RATE_LIMIT_CHAT_PER_MIN: int = 20

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.LLM_PROVIDER != "none" and bool(self.LLM_API_KEY)

    @property
    def cdse_enabled(self) -> bool:
        return bool(self.CDSE_CLIENT_ID and self.CDSE_CLIENT_SECRET)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
