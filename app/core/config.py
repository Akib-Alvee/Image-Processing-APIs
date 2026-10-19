from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application settings loaded and validated strictly from .env / environment variables."""

    # App Info (non-sensitive defaults)
    APP_NAME: str = "Cloudinary-Clone Image Processing API"
    APP_ENV: str = "development"
    DEBUG: bool = False

    # PostgreSQL Database URLs (Required from .env)
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # JWT Security Settings (Required from .env — never hardcode secrets in code)
    JWT_SECRET_KEY: str
    JWT_REFRESH_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # MinIO / S3 / Cloudflare R2 Object Storage Settings (Required from .env)
    S3_ENDPOINT_URL: str
    S3_PUBLIC_URL: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_BUCKET_NAME: str
    S3_REGION: str = "us-east-1"

    # Redis & RabbitMQ Settings (Required from .env)
    REDIS_URL: str
    CELERY_RESULT_BACKEND: str
    CELERY_BROKER_URL: str

    # Rate Limiting, Caching & Upload Constraints
    RATE_LIMIT_UPLOAD_PER_MINUTE: int = 20
    RATE_LIMIT_TRANSFORM_PER_MINUTE: int = 10
    CACHE_TTL_SECONDS: int = 3600
    MAX_UPLOAD_SIZE_MB: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings so .env is only parsed once."""
    return Settings()


settings = get_settings()
