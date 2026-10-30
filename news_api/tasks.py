"""Asynchronous job executors (crawl + single-URL extraction)."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from pathlib import Path
from typing import Any

from gne import GeneralNewsExtractor

import news_crawler
import url_extract
from news_api.netguard import ensure_url_allowed
from news_api.settings import Settings

LOG = logging.getLogger("news_api.tasks")


def execute_job(job_type: str, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if job_type == "crawl":
        return execute_crawl(payload, settings)
    if job_type == "extract":
        return execute_extract(payload, settings)
    raise ValueError(f"未知任务类型: {job_type}")


def _init_db(settings: Settings) -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = news_crawler.init_db(settings.db_path)
    conn.close()


def execute_crawl(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    config_path = news_crawler.resolve_config_path(settings.config_path)
    config = news_crawler.load_config(config_path)
    sources = list(config.get("sources") or [])
    source_filter = payload.get("source")
    if source_filter:
        sources = [item for item in sources if item.get("name") == source_filter]
        if not sources:
            names = [item.get("name") for item in config.get("sources", [])]
            raise ValueError(f"找不到站点 {source_filter}；已配置：{names}")
    if not sources:
        raise ValueError(f"{config_path} 里没有配置任何站点")

    _init_db(settings)
    tz_name = str(config.get("timezone") or settings.timezone)
    tz = news_crawler.get_timezone(tz_name)
    since = payload.get("since")
    if since:
        cutoff = dt.datetime.strptime(str(since), "%Y-%m-%d").replace(tzinfo=tz)
    else:
        days = int(payload.get("days") or config.get("days", 3))
        cutoff = dt.datetime.now(tz) - dt.timedelta(days=days)

    workers = int(payload.get("workers") or config.get("workers", 3))
    workers = max(1, min(workers, len(sources)))
    limit_per_source = int(payload.get("limit_per_source") or 0)
    force = bool(payload.get("force"))

    results: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                news_crawler.run_source,
                raw_source,
                config,
                settings.db_path,
                cutoff,
                force,
                limit_per_source,
            ): raw_source
            for raw_source in sources
        }
        for future in concurrent.futures.as_completed(futures):
            raw_source = futures[future]
            try:
                name, counts = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate one bad site
                LOG.exception("站点任务失败: %s", raw_source.get("name"))
                name = str(raw_source.get("name") or "unknown")
                counts = {"error": 1}
                results.append({"name": name, "counts": counts, "error": str(exc)[:500]})
            else:
                results.append({"name": name, "counts": counts})
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value

    results.sort(key=lambda item: item["name"])
    return {
        "config": config_path,
        "cutoff": cutoff.isoformat(),
        "sources": results,
        "totals": totals,
    }


def execute_extract(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    url = str(payload["url"])
    source = str(payload.get("source") or "on_demand")
    ensure_url_allowed(url, allow_private=settings.allow_private_urls)
    validator = (
        None
        if settings.allow_private_urls
        else (lambda target: ensure_url_allowed(target, allow_private=False))
    )
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = news_crawler.init_db(settings.db_path)
    try:
        result = url_extract.extract_article(
            url,
            GeneralNewsExtractor(),
            allow_browser=settings.extract_allow_browser,
            url_validator=validator,
        )
        if result is None:
            news_crawler.save_article(
                conn,
                source,
                url,
                status="error",
                error="无法抓取或抽取正文",
            )
            raise RuntimeError("无法抓取或抽取正文，已记录为 error，可稍后重试")

        publish_text = news_crawler.clean_text(result.get("publish_time"))
        publish_dt = news_crawler.parse_datetime(publish_text, None, settings.timezone)
        if publish_dt is not None:
            publish_text = publish_dt.strftime("%Y-%m-%d %H:%M:%S")
        status = "ok" if publish_dt is not None else "unknown_date"
        error = "" if publish_dt is not None else "无法解析发布日期"
        article_id = news_crawler.save_article(
            conn,
            source,
            url,
            title=news_crawler.clean_text(result.get("title")),
            publish_time=publish_text,
            author=news_crawler.clean_text(result.get("author")),
            content=news_crawler.clean_text(result.get("content")),
            images=result.get("images") or [],
            status=status,
            error=error,
        )
        return {
            "article_id": article_id,
            "source": source,
            "url": url,
            "status": status,
            "title": news_crawler.clean_text(result.get("title")),
            "published_at": publish_dt.isoformat() if publish_dt else None,
            "content_length": len(news_crawler.clean_text(result.get("content"))),
            "is_video": bool(result.get("is_video")),
            "error": error or None,
        }
    finally:
        conn.close()
