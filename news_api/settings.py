"""Runtime settings for the news API."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str = "news.db"
    config_path: str = "sources.json"
    api_key: str | None = None
    job_workers: int = 1
    timezone: str = "Asia/Shanghai"
    cors_origins: tuple[str, ...] = ()
    allow_private_urls: bool = False
    max_pending_jobs: int = 50
    extract_allow_browser: bool = False
    require_auth: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = os.getenv("NEWS_DB") or os.getenv("DB_PATH") or "news.db"
        config_path = os.getenv("NEWS_CONFIG") or "sources.json"
        api_key = os.getenv("NEWS_API_KEY", "").strip() or None
        origins = tuple(
            item.strip()
            for item in os.getenv("NEWS_CORS_ORIGINS", "").split(",")
            if item.strip()
        )
        return cls(
            db_path=db_path,
            config_path=config_path,
            api_key=api_key,
            job_workers=max(1, _env_int("NEWS_JOB_WORKERS", 1)),
            timezone=os.getenv("NEWS_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
            cors_origins=origins,
            allow_private_urls=_env_bool("NEWS_ALLOW_PRIVATE_URLS"),
            max_pending_jobs=max(1, _env_int("NEWS_MAX_PENDING_JOBS", 50)),
            extract_allow_browser=_env_bool("NEWS_EXTRACT_ALLOW_BROWSER"),
            require_auth=_env_bool("NEWS_REQUIRE_AUTH"),
        )
