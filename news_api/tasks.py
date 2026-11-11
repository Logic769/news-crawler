"""Asynchronous job executors (crawl + single-URL extraction)."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
from pathlib import Path
from typing import Any

import news_crawler
import url_extract
from gne import GeneralNewsExtractor
from news_api.db import load_timezones
from news_api.netguard import ensure_url_allowed
from news_api.settings import Settings
from url_extract import close_thread_browser, normalize_images_for_update

LOG = logging.getLogger("news_api.tasks")



def execute_job(job_type: str, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if job_type == "crawl":
        return execute_crawl(payload, settings)
    if job_type == "extract":
        return execute_extract(payload, settings)
    raise ValueError(f"未知任务类型: {job_type}")


def _load_config_quietly(settings: Settings) -> dict[str, Any]:
    """读取站点配置；配置损坏时返回空字典（宁可拒绝自定义 source）。"""
    try:
        config = news_crawler.load_config(
            news_crawler.resolve_config_path(settings.config_path)
        )
    except Exception:
        LOG.warning("读取站点配置失败，extract 作业将退回 GNE 默认抽取", exc_info=True)
        return {}
    return config


def _configured_source_names(
    settings: Settings, config: dict[str, Any] | None = None
) -> set[str]:
    """已配置的站点名集合；配置损坏时返回空集（宁可拒绝自定义 source）。"""
    if config is None:
        config = _load_config_quietly(settings)
    return {
        str(item.get("name")).strip()
        for item in config.get("sources", [])
        if str(item.get("name") or "").strip()
    }


def _source_config_for(
    settings: Settings, source_name: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """站点配置字典；on_demand 或配置缺失时为空字典。"""
    if not source_name:
        return {}
    if config is None:
        config = _load_config_quietly(settings)
    for item in config.get("sources") or []:
        if str(item.get("name") or "").strip() == source_name:
            return item
    return {}


def _init_db(settings: Settings) -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = news_crawler.init_db(settings.db_path)
    conn.close()


def execute_crawl(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    try:
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
    finally:
        # 无论前面哪一步抛错（配置加载/建库/并发执行），都释放本线程可能
        # 复用的浏览器，避免作业线程上的浏览器资源泄漏。
        close_thread_browser()


def execute_extract(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """执行 extract 作业：校验来源与 URL，抽取正文后写库并返回结果摘要。"""
    url = str(payload["url"])
    requested_source = str(payload.get("source") or "").strip()
    config = _load_config_quietly(settings)
    if requested_source:
        # 只允许写入真实存在的站点，否则任意调用方都能把任意 URL 记成别的
        # 站点，污染 /v1/sources 与 /v1/stats 的统计。
        allowed = _configured_source_names(settings, config)
        if requested_source not in allowed:
            raise ValueError(
                f"source={requested_source!r} 不是已配置的站点；"
                f"留空表示 on_demand。已配置：{sorted(allowed)}"
            )
        source = requested_source
    else:
        source = "on_demand"
    # extract 落库的墙钟/时区必须与该 URL 所属站点一致：source 为配置站点时用
    # 站点自身 timezone，否则（on_demand）才回落到全局 settings.timezone。
    if requested_source:
        tz_name = load_timezones(settings.config_path, settings.timezone).get(
            requested_source, settings.timezone
        )
    else:
        tz_name = settings.timezone
    ensure_url_allowed(url, allow_private=settings.allow_private_urls)
    validator = (
        None
        if settings.allow_private_urls
        else (lambda target: ensure_url_allowed(target, allow_private=False))
    )
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = news_crawler.init_db(settings.db_path)
    try:
        source_config = _source_config_for(settings, source, config)
        title_xpath = str(source_config.get("article_title_xpath") or "").strip()
        publish_time_xpath = str(
            source_config.get("article_publish_time_xpath") or ""
        ).strip()
        try:
            date_skew_days = max(
                0, min(365, int(source_config.get("date_skew_days", 7)))
            )
        except (TypeError, ValueError):
            date_skew_days = 7
        result = url_extract.extract_article(
            url,
            GeneralNewsExtractor(),
            allow_browser=settings.extract_allow_browser,
            url_validator=validator,
            title_xpath=title_xpath,
            publish_time_xpath=publish_time_xpath,
        )
        if result is None:
            # 只标记错误：save_article 是部分更新，已有正文不会被清空。
            news_crawler.save_article(
                conn,
                source,
                url,
                status="error",
                error="无法抓取或抽取正文",
            )
            raise RuntimeError("无法抓取或抽取正文，已记录为 error，可稍后重试")

        raw_content = str(result.get("content") or "").strip()
        publish_raw = news_crawler.clean_text(result.get("publish_time"))
        publish_dt = news_crawler.parse_datetime(
            publish_raw, None, tz_name, allow_year_adjust=False
        )
        publish_text = (
            news_crawler.format_publish_time(publish_dt, tz_name)
            if publish_dt is not None
            else ""
        )
        raw_publish_source = str(result.get("publish_time_source") or "")
        publish_source = news_crawler.publish_source_for(raw_publish_source)
        # 与 crawler 的 process_entry 保持一致：重抓已入库文章时，若详情日期
        # 与库里的列表日期相差过大，说明详情抽到了正文/模板里的错误日期，
        # 回退到列表日期，避免把已校准的时间改坏；详情日期完全解析失败时，
        # 也用库里的列表日期兜底。
        stored_list_dt: dt.datetime | None = None
        if requested_source:
            row = conn.execute(
                "SELECT list_publish_time FROM articles WHERE source = ? AND url = ?",
                (source, url),
            ).fetchone()
            stored_list_time = str(row[0] or "").strip() if row else ""
            if stored_list_time:
                list_dt = news_crawler.parse_datetime(
                    stored_list_time, None, tz_name, allow_year_adjust=False
                )
                if list_dt is not None:
                    stored_list_dt = list_dt
                    if publish_dt is None:
                        publish_dt = list_dt
                        publish_text = news_crawler.format_publish_time(list_dt, tz_name)
                        publish_source = "list"
                    else:
                        skew = abs(
                            (
                                publish_dt.astimezone(news_crawler.get_timezone(tz_name)).date()
                                - list_dt.astimezone(news_crawler.get_timezone(tz_name)).date()
                            ).days
                        )
                        if skew > date_skew_days:
                            LOG.warning(
                                "extract 作业详情日期 %s 与列表日期 %s 相差 %d 天，改用列表日期",
                                publish_dt.isoformat(),
                                list_dt.isoformat(),
                                skew,
                            )
                            publish_dt = list_dt
                            publish_text = news_crawler.format_publish_time(list_dt, tz_name)
                            publish_source = "list"
        # 已回退到列表日期的文章，年份来自库里的可信列表日期，不能再按
        # 详情原文的无年份文本判一次 unknown_date，否则会把刚救回来的状态覆盖掉。
        if (
            publish_dt is not None
            and publish_source != "list"
            and not news_crawler.has_explicit_year(publish_raw)
        ):
            if stored_list_dt is not None:
                reconciled = news_crawler.align_yearless_datetime(
                    publish_dt, stored_list_dt, tz_name
                )
                if reconciled is not None:
                    publish_dt = reconciled
                    publish_text = news_crawler.format_publish_time(reconciled, tz_name)
                else:
                    publish_dt = stored_list_dt
                    publish_text = news_crawler.format_publish_time(stored_list_dt, tz_name)
                    publish_source = "list"
            else:
                LOG.warning("extract 作业发布时间缺少年份，记为 unknown_date: %r", publish_raw)
                publish_dt = None
                publish_text = ""
                publish_source = ""
        if publish_dt is None:
            publish_source = ""
        title_source = str(result.get("title_source") or "gne_default")
        if title_source not in ("article_title_xpath", "gne_default"):
            title_source = "gne_default"
        status = "ok" if publish_dt is not None else "unknown_date"
        error = "" if publish_dt is not None else "无法解析发布日期"
        article_id = news_crawler.save_article(
            conn,
            source,
            url,
            title=news_crawler.clean_text(result.get("title")),
            publish_time=publish_text,
            publish_time_source=publish_source,
            publish_time_raw=publish_raw,
            title_source=title_source,
            # unknown_date 没有可信发布时间，不写站点时区；与 crawler 保持一致。
            timezone=tz_name if status == "ok" else "",
            author=news_crawler.clean_text(result.get("author")),
            content=news_crawler.clean_text(raw_content),
            raw_content=raw_content,
            images=normalize_images_for_update(result.get("images"), url),
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
            "content_length": len(news_crawler.clean_text(raw_content)),
            "is_video": bool(result.get("is_video")),
            "error": error or None,
        }
    finally:
        conn.close()
        # extract 默认禁用浏览器回退；开启后同样要在作业结束时释放，
        # 否则长期运行的 API 进程会在线程局部一直挂着一个 Chromium。
        close_thread_browser()
