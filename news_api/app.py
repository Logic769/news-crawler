"""FastAPI application exposing the news-crawler read model and jobs."""

from __future__ import annotations

import datetime as dt
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import news_crawler
from news_api import __version__
from news_api.db import (
    count_articles,
    get_article,
    list_sources,
    load_timezones,
    ping_database,
    query_articles,
    stats,
)
from news_api.jobs import JobManager, JobQueueFull
from news_api.models import (
    ArticleListResponse,
    ArticleOut,
    HealthOut,
    JobCreate,
    JobCreateResponse,
    JobListResponse,
    JobOut,
    SourceListResponse,
    StatsOut,
)
from news_api.netguard import UnsafeUrlError, ensure_url_allowed
from news_api.settings import Settings
from news_api.timeutil import normalize_db_bound, resolve_timezone

LOG = logging.getLogger("news_api")

# health 的 COUNT(*) 缓存需要锁：多个请求同时发现缓存过期时，只允许一个真正
# 执行全表计数，其余复用刚写回的结果。
_HEALTH_COUNT_LOCK = threading.Lock()


def _cors_headers_for(request: Request) -> dict[str, str] | None:
    """Return CORS headers for responses produced outside CORSMiddleware.

    Starlette calls the generic Exception handler from ServerErrorMiddleware,
    which sits outside user middleware, so a 500 response would otherwise miss
    the CORS headers that normal responses get. Keep the same origin allow-list.
    """
    origin = request.headers.get("origin")
    if not origin:
        return None
    allowed = request.app.state.settings.cors_origins
    if "*" in allowed:
        return {"Access-Control-Allow-Origin": "*"}
    if origin in allowed:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return None


def require_api_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_key
    if not expected:
        if settings.require_auth:
            raise HTTPException(
                status_code=401,
                detail="服务未设置 NEWS_API_KEY，已按 NEWS_REQUIRE_AUTH 拒绝未鉴权访问",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return
    provided = request.headers.get("x-api-key", "").strip()
    if not provided:
        auth = request.headers.get("authorization", "").strip()
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="缺少或无效的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _timezones(request: Request) -> tuple[dict[str, str], str]:
    settings: Settings = request.app.state.settings
    return load_timezones(settings.config_path, settings.timezone), settings.timezone


def _bound(value: str | None, tz_name: str, *, end_of_day: bool) -> str | None:
    if not value:
        return None
    try:
        return normalize_db_bound(value, tz_name, end_of_day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"时间参数不合法: {value}") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = news_crawler.init_db(settings.db_path)
        conn.close()
        manager = JobManager(settings)
        app.state.settings = settings
        app.state.jobs = manager
        if not settings.api_key:
            if settings.require_auth:
                LOG.warning(
                    "NEWS_API_KEY 未设置且 NEWS_REQUIRE_AUTH=1：业务接口将返回 401"
                )
            else:
                LOG.warning(
                    "NEWS_API_KEY 未设置：/v1 接口无鉴权，仅在可信网络中暴露"
                )
        timezones = load_timezones(settings.config_path, settings.timezone)
        foreign = sorted({tz for tz in timezones.values() if tz != settings.timezone})
        if foreign:
            LOG.warning(
                "站点时区 %s 与 NEWS_TIMEZONE=%s 不一致：列表排序按站点本地墙钟，时间过滤按真实时刻(UTC)",
                ",".join(foreign),
                settings.timezone,
            )
        manager.start()
        try:
            yield
        finally:
            manager.shutdown()

    app = FastAPI(
        title="news-crawler API",
        version=__version__,
        description="新闻抓取与标准结构化查询接口",
        lifespan=lifespan,
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "status": exc.status_code,
                    "message": str(exc.detail),
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：未捕获异常也必须返回统一的 JSON 错误体，而不是 Starlette
        # 默认的纯文本 "Internal Server Error"（下游无法按 code 处理）。
        LOG.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "status": 500,
                    "message": "服务器内部错误",
                }
            },
            headers=_cors_headers_for(request),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "status": 422,
                    "message": "请求参数不合法",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    auth = [Depends(require_api_key)]

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": "news-crawler API",
            "version": __version__,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/v1/health",
        }

    @app.get("/v1/health", response_model=HealthOut)
    def health(request: Request) -> Any:
        runtime: Settings = request.app.state.settings
        state = request.app.state
        try:
            # 先做 O(1) 的存活探测：数据库不可用时立刻 503，不受计数缓存影响。
            ping_database(runtime.db_path)
            database = "ok"
            # COUNT(*) 是全表扫描，健康检查又无需鉴权，任何客户端都能靠高频
            # 轮询把数据库打满。这里只缓存计数结果 30s，存活状态仍然每次实测。
            cached = getattr(state, "health_count_cache", None)
            now = time.monotonic()
            if cached is None or now - cached[0] >= 30:
                with _HEALTH_COUNT_LOCK:
                    # 锁内二次检查：并发请求同时看到过期时，只让第一个计数。
                    cached = getattr(state, "health_count_cache", None)
                    now = time.monotonic()
                    if cached is None or now - cached[0] >= 30:
                        total = count_articles(runtime.db_path)
                        state.health_count_cache = (now, total)
                    else:
                        total = cached[1]
            else:
                total = cached[1]
        except Exception as exc:  # noqa: BLE001 - health reports degradation
            LOG.warning("健康检查读取数据库失败: %s", exc)
            total = 0
            database = "error"
        payload = {
            "status": "ok",
            "api_version": __version__,
            "database": database,
            "articles": total,
            "time": dt.datetime.now(resolve_timezone(runtime.timezone))
            .replace(microsecond=0)
            .isoformat(),
        }
        if database != "ok":
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "database_unavailable",
                        "status": 503,
                        "message": "数据库不可用",
                    }
                },
            )
        return payload

    @app.get(
        "/v1/articles",
        response_model=ArticleListResponse,
        dependencies=auth,
    )
    def articles(
        request: Request,
        status: str = Query(
            "ok", pattern="^(ok|old|no_content|unknown_date|error|all)$"
        ),
        source: str | None = Query(None, max_length=100),
        published_from: str | None = Query(None, max_length=40),
        published_to: str | None = Query(None, max_length=40),
        deadline_from: str | None = Query(None, max_length=40),
        deadline_to: str | None = Query(None, max_length=40),
        q: str | None = Query(None, max_length=200),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        order: str = Query(
            "published_desc",
            pattern="^(published_desc|published_asc|updated_desc|deadline_desc|deadline_asc)$",
        ),
        include_content: bool = False,
    ) -> dict[str, Any]:
        timezones, default_tz = _timezones(request)
        data, meta = query_articles(
            request.app.state.settings.db_path,
            status=status,
            source=source,
            published_from=_bound(published_from, default_tz, end_of_day=False),
            published_to=_bound(published_to, default_tz, end_of_day=True),
            deadline_from=_bound(deadline_from, default_tz, end_of_day=False),
            deadline_to=_bound(deadline_to, default_tz, end_of_day=True),
            q=q,
            page=page,
            page_size=page_size,
            order=order,
            include_content=include_content,
            timezones=timezones,
            default_tz=default_tz,
        )
        return {"data": data, "meta": meta}

    @app.get(
        "/v1/articles/{article_id}",
        response_model=ArticleOut,
        dependencies=auth,
    )
    def article_detail(article_id: str, request: Request) -> dict[str, Any]:
        timezones, default_tz = _timezones(request)
        article = get_article(
            request.app.state.settings.db_path,
            article_id,
            timezones=timezones,
            default_tz=default_tz,
        )
        if article is None:
            raise HTTPException(status_code=404, detail="文章不存在")
        return article

    @app.get("/v1/sources", response_model=SourceListResponse, dependencies=auth)
    def sources(request: Request) -> dict[str, Any]:
        timezones, default_tz = _timezones(request)
        return {
            "data": list_sources(
                request.app.state.settings.db_path,
                timezones=timezones,
                default_tz=default_tz,
            )
        }

    @app.get("/v1/stats", response_model=StatsOut, dependencies=auth)
    def statistics(request: Request) -> dict[str, Any]:
        return stats(
            request.app.state.settings.db_path,
            default_tz=request.app.state.settings.timezone,
        )

    @app.post(
        "/v1/jobs",
        response_model=JobCreateResponse,
        status_code=202,
        dependencies=auth,
    )
    def create_job(payload: JobCreate, request: Request) -> dict[str, Any]:
        body = payload.model_dump(mode="json", exclude_none=True)
        job_type = str(body.pop("type"))
        if job_type == "extract":
            try:
                ensure_url_allowed(
                    str(body.get("url", "")),
                    allow_private=request.app.state.settings.allow_private_urls,
                )
            except UnsafeUrlError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            job = request.app.state.jobs.create(job_type, body)
        except JobQueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"data": job}

    @app.get("/v1/jobs", response_model=JobListResponse, dependencies=auth)
    def jobs(
        request: Request,
        status: str | None = Query(
            None, pattern="^(queued|running|succeeded|failed)$"
        ),
        type: str | None = Query(None, pattern="^(crawl|extract)$"),
        page: int = Query(1, ge=1, le=1_000_000),
        page_size: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        rows, total = request.app.state.jobs.list(
            status=status,
            job_type=type,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {
            "data": rows,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page * page_size < total,
            },
        }

    @app.get("/v1/jobs/{job_id}", response_model=JobOut, dependencies=auth)
    def job_detail(job_id: str, request: Request) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job

    return app


app = create_app()
