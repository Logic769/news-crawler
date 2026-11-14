"""Pydantic request/response models for the public API."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal
from urllib.parse import urlsplit

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
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("url 不允许包含控制字符")
        if not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("url 必须是 http:// 或 https:// 开头")
        try:
            parts = urlsplit(value)
        except ValueError as exc:
            raise ValueError(f"url 格式不合法: {exc}") from exc
        if parts.username is not None or parts.password is not None:
            raise ValueError("url 不允许包含用户信息(user:pass@)")
        if not parts.hostname:
            raise ValueError("url 缺少主机名")
        return value

    @model_validator(mode="after")
    def _check_type_fields(self) -> JobCreate:
        if self.type == "extract":
            if not self.url:
                raise ValueError("extract 任务必须提供 url")
            # extract 只抓单个 URL，crawl 的窗口/并发/批量参数对它无意义；
            # 显式传入（而非默认值）时一律拒绝，避免静默忽略造成预期落空。
            crawl_only = {"days", "since", "force", "limit_per_source", "workers"}
            extra = sorted(
                field
                for field in crawl_only & set(self.model_fields_set)
                if getattr(self, field) not in (None, False)
            )
            if extra:
                raise ValueError(f"extract 任务不接受字段：{extra}")
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
    publish_time_source: str = ""
    publish_time_raw: str = ""
    list_publish_time: str = ""
    title_source: str = ""
    # 项目申报列表特化字段：新闻站点为空字符串。
    deadline: str = ""
    deadline_raw: str = ""
    project_status: str = ""
    timezone: str
    content: str | None = None
    # 保留段落换行的原始正文：只在详情返回，列表接口（include_content=false）为 null。
    raw_content: str | None = None
    # 清洗后的正文 HTML（白名单标签/属性）：只在详情返回，前端据此渲染富文本。
    body_html: str | None = None
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
