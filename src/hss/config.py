"""Settings from environment variables (prefix HSS_) with laptop defaults."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HSS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Stores
    opensearch_url: str = "http://localhost:9201"
    index: str = "products"
    redis_url: str = "redis://localhost:6381/0"

    # Data
    raw_dir: Path = Path("data/raw")
    prepared_dir: Path = Path("data/prepared")
    models_dir: Path = Path("data/models")
    reports_dir: Path = Path("reports")
    catalog_size: int = 200_000
    eval_queries: int = 2000
    seed: int = 42

    # Models
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top: int = 50
    rrf_k: int = 60
    candidates: int = 100  # per retriever before fusion
    knn_ef_search: int = 256  # HNSW beam per query; one merged segment needs more than the default (= k) to keep recall

    # API
    rate_limit: str = "120/minute"
    admin_key: str = ""
    cache_ttl_s: int = 300
    cors_origins: str = "http://localhost:5173,http://localhost:4310"
    log_level: str = "INFO"
    service_name: str = "hss-api"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    settings.cache_clear()
