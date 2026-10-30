"""Pydantic request/response models for the public API."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobType = Literal["crawl", "extract"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


class JobCreate(BaseModel):
    """Create an asynchronous crawl or single-URL extraction job."""

    model_config = ConfigDict(extra="forbid")

    type: JobType
    source: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2048)
    days: int | None = Field(default=None, ge=1, le=365)
    since: dt.date | None = None
    force: bool = False
    limit_per_source: int | None = Field(default=None, ge=1, le=1000)
    workers: int | None = Field(default=None, ge=1, le=16)

    @field_validator("source")
    @classmethod
    def _normalize_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("url 必须是 http:// 或 https:// 开头")
        return value

    @model_validator(mode="after")
    def _check_type_fields(self) -> "JobCreate":
        if self.type == "extract":
            if not self.url:
                raise ValueError("extract 任务必须提供 url")
        elif self.url:
            raise ValueError("crawl 任务不接受 url 字段")
        return self


class ArticleOut(BaseModel):
    id: str
    source: str
    url: str
    title: str
    list_title: str
    author: str
    published_at: str | None = None
    published_at_utc: str | None = None
    published_raw: str
    timezone: str
    content: str | None = None
    content_length: int = 0
    images: list[str] = Field(default_factory=list)
    status: str
    error: str | None = None
    fetched_at: str | None = None
    updated_at: str | None = None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class ArticleListResponse(BaseModel):
    data: list[ArticleOut]
    meta: PageMeta


class SourceOut(BaseModel):
    source: str
    total: int
    ok: int
    latest_published_at: str | None = None
    latest_updated_at: str | None = None


class SourceListResponse(BaseModel):
    data: list[SourceOut]


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    sources: int
    latest_published_at: str | None = None
    generated_at: str


class JobOut(BaseModel):
    id: str
    type: str
    status: JobStatus
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class JobCreateResponse(BaseModel):
    data: JobOut


class JobListResponse(BaseModel):
    data: list[JobOut]
    meta: PageMeta


class HealthOut(BaseModel):
    status: str
    api_version: str
    database: str
    articles: int
    time: str
