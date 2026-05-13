"""Application settings for edit2ppt server.

Reads from environment variables (and .env) using pydantic-settings.
All env var names use the EDIT2PPT_ prefix to avoid collisions.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level server configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EDIT2PPT_",
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Default language for new projects when none is specified
    default_lang: str = "ko-KR"

    # Database (asyncpg DSN). Defaults to local docker-compose Postgres.
    database_url: str = "postgresql+asyncpg://edit2ppt:edit2ppt@localhost:5432/edit2ppt"

    # Redis (for job queue + cache)
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (S3-compatible)
    s3_endpoint_url: str | None = None  # None = real AWS S3; set to http://minio:9000 for local
    s3_region: str = "us-east-1"
    s3_bucket: str = "edit2ppt-local"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_public_base_url: str | None = None  # if presigned URLs are exposed via a CDN

    # Auth
    auth_dev_api_key: str | None = Field(
        default=None,
        description="Dev-only static API key. M0 stub; replaced by tenant-issued keys in M6.",
    )

    # Asset TTLs (seconds). Aligned with ppt-master-analysis/04 §4.7.
    asset_ttl_source_seconds: int = 7 * 24 * 60 * 60
    asset_ttl_intermediate_seconds: int = 30 * 24 * 60 * 60
    asset_ttl_pptx_seconds: int = 90 * 24 * 60 * 60

    # Max upload size (bytes)
    max_upload_size_bytes: int = 50 * 1024 * 1024  # 50 MB


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
