"""Multi-site news crawler built on GNE.

Workflow:
1. Read site definitions from a JSON config file.
2. Fetch each site's list page(s) and parse title / URL / publish time.
3. Keep only articles published within the last N days.
4. Fetch every article page and extract title/content with GNE,
   falling back to headless Chrome (Playwright) when needed.
5. Save results into SQLite and skip URLs already seen.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import re
import socket
import sqlite3
import sys
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import lxml.html
from lxml import etree

from gne import GeneralNewsExtractor
from gne.netguard import UnsafeUrlError, ensure_url_allowed
from url_extract import (
    BROWSER_FALLBACK_HTTP_CODES,
    MIN_CONTENT_LENGTH,
    close_thread_browser,
    content_is_too_short,
    extract_from_html,
    fetch_html,
    fetch_html_with_browser,
    normalize_images_for_update,
)

LOG = logging.getLogger("news_crawler")

DEFAULT_SOURCES_FILE = "sources.json"
DEFAULT_DB_FILE = "news.db"
# 锁等待时长与 sqlite3.connect(timeout=...) 保持一致，避免 PRAGMA 把
# 30s 的连接超时悄悄压成 5s（并发写时更早报 database is locked）。
DB_BUSY_TIMEOUT_MS = 30000
SCHEMA_VERSION = 8

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    article_id   TEXT,
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    list_title   TEXT,
    title        TEXT,
    publish_time TEXT,
    -- 真实时刻（UTC，YYYY-MM-DD HH:MM:SS）：混时区站点按墙钟比较会错位，
    -- 统一存 UTC 供时间过滤；publish_time 仍是站点本地墙钟，供展示与排序。
    publish_time_utc TEXT,
    -- 发布时间来源与原始文本：xpath/labeled_text/meta/text/video_meta/detail/list，便于排查站点改版。
    publish_time_source TEXT,
    publish_time_raw TEXT,
    -- 列表页给出的日期，用于与详情日期交叉校验。
    list_publish_time TEXT,
    -- 标题来源：article_title_xpath / gne_default。
    title_source TEXT,
    -- 项目申报列表特化：截止时间与申报状态（news 站点为 NULL）。
    -- deadline 存站点本地墙钟，deadline_raw 存列表原文。
    deadline     TEXT,
    deadline_raw TEXT,
    project_status TEXT,
    timezone     TEXT,
    author       TEXT,
    content      TEXT,
    content_length INTEGER,
    raw_content  TEXT,
    images       TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    first_seen_at TEXT,
    updated_at   TEXT,
    PRIMARY KEY (source, url)
);

CREATE INDEX IF NOT EXISTS idx_articles_source
    ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_status
    ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_publish_time
    ON articles(publish_time);
-- 查询性能覆盖索引由 _PERFORMANCE_INDEX_SQL 建（见 init_db 的迁移事务），
-- 不写在这里：executescript 会隐式提交，放进 SCHEMA_SQL 会让建索引落在
-- BEGIN IMMEDIATE 之外，多进程并发首启老库时可能撞上 database is locked。

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status
    ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_created_at
    ON crawl_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_dedupe
    ON crawl_jobs(type, payload, status);
"""


# 查询性能覆盖索引。放在迁移事务内执行，新建库（user_version=0）同样会走到，
# 与老库升级路径一致，避免两条路径建出不同的索引集合。
# - (status, publish_time DESC, source, url)：列表分页。没有它时 SQLite 要取出
#   全部 status='ok' 的行再 TEMP B-TREE 排序（10 万行实测 220ms），有它之后
#   索引本身即最终顺序，实测 0.1ms。
# - (source, publish_time DESC, updated_at DESC, timezone, status)：/v1/sources
#   的分组与相关子查询，10 万行实测 3.8s → 56ms。
_PERFORMANCE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_articles_status_published "
    "ON articles(status, publish_time DESC, source, url)",
    "CREATE INDEX IF NOT EXISTS idx_articles_source_cover "
    "ON articles(source, publish_time DESC, updated_at DESC, timezone, status)",
    # order=updated_desc 的列表分页：没有它时 SQLite 取全部 status='ok' 行再
    # TEMP B-TREE 全表排序；有它之后索引本身即最终顺序。
    "CREATE INDEX IF NOT EXISTS idx_articles_status_updated "
    "ON articles(status, updated_at DESC, source, url)",
    # v6：时间过滤改用 publish_time_utc；放迁移事务内建，老库先 ALTER 补列再建索引。
    "CREATE INDEX IF NOT EXISTS idx_articles_publish_time_utc "
    "ON articles(publish_time_utc)",
    # v8：项目申报公告按 deadline 排序/过滤（项目行 publish_time 为空）。
    "CREATE INDEX IF NOT EXISTS idx_articles_deadline "
    "ON articles(deadline)",
)


def now_iso() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def article_id_for(source: str, url: str) -> str:
    """Return a stable, URL-safe article id derived from source + url."""
    digest = hashlib.sha256(f"{source}\x1f{url}".encode()).hexdigest()
    return digest[:32]


def get_timezone(name: str | None):
    if not name:
        return dt.datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


# 只清理常见 HTML 标签名，避免把标题里真的出现的“A < B > C”当成标签删掉。
# 多字母标签名（br/p/div/…）不会出现在普通标题里，带不带属性都直接删。
_TAG_LITERAL_RE = re.compile(
    # 尖括号后必须紧跟标签名：真标签几乎不写 "< br >"，而标题里的
    # "a < b > c" 这类比较表达式都带空格，这样就不会把两者混淆。
    # 属性窗口不设上限：[^<>] 已排除尖括号，`*` 后接 `/?>` 是线性匹配，
    # 不会回溯爆炸；限长（原 40 字符）会漏掉带长 style 的标签，留下半截开标签。
    r"</?(?:br|p|div|span|strong|em|sub|sup)(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)
# 单字母标签（b/i/u/a）与数学比较式 "A <B> C" 形状完全相同，分两步删：
# 1) 成对的无属性写法 "<b>加粗</b>" 只保留中间文本；
# 2) 其余带属性、自闭和或孤立的结束标签仍然删掉（"<b class=x>"、"</b>"）。
_PAIRED_LETTER_TAG_RE = re.compile(r"<([biua])>([^<>]*)</\1>", re.IGNORECASE)
_LETTER_TAG_RE = re.compile(
    r"</[biua]>|<[biua]\s[^<>]*/?>|<[biua]/>",
    re.IGNORECASE,
)


def clean_title(value: Any) -> str:
    """清理标题里的字面量 HTML 标签。

    部分站点把标题写在 HTML 属性里（例如 `<div title="…"></div>`），属性值
    中的 `<br>` 不会被浏览器解析，会被原样抓出来；这类标记不是正文内容，
    直接删掉而不是压成空格，避免「填报 正式申报书」这种断词。
    """
    text = clean_text(value)
    if not text:
        return ""
    text = _PAIRED_LETTER_TAG_RE.sub(r"\2", text)
    text = _TAG_LITERAL_RE.sub("", text)
    return clean_text(_LETTER_TAG_RE.sub("", text))


def has_explicit_year(value: Any) -> bool:
    """判断日期文本是否自带年份（含解析器支持的两位年份）。"""
    text = clean_text(value)
    if re.search(r"(?:19|20)\d{2}", text):
        return True
    # parse_datetime 支持 18-03-08 / 18年3月8日 这类两位年份，
    # 不能把它们当成缺年份的月日再补一次年。
    return bool(
        re.search(
            r"(?<!\d)\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?(?!\d)",
            text,
        )
    )


def is_yearless_month_day(value: Any) -> bool:
    """是否是需要按基准年份补齐的“月日”文本。

    “昨天”“3小时前”等相对时间已由 parse_datetime 解析为完整日期，
    不能因为文本里没有年份就清空；带两位年份的日期也不算缺年份。
    """
    text = clean_text(value)
    if not text or has_explicit_year(text):
        return False
    if re.search(r"刚刚|昨天|前天|\d+\s*(?:分钟|小时|天)前", text):
        return False
    return bool(
        re.search(r"(?<!\d)\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?", text)
    )


# 详情抽取器报告的来源 -> 落库展示用的三档来源。
# 解析型来源（meta/json_ld/time_tag）统一归为 meta，避免暴露内部实现细节。
_PUBLISH_SOURCE_MAP = {
    "xpath": "xpath",
    "labeled_text": "labeled_text",
    "meta": "meta",
    "json_ld": "meta",
    "time_tag": "meta",
    "text": "text",
    "video_meta": "video_meta",
}


def publish_source_for(raw: str) -> str:
    """Map a GNE publish-time source to the stored publish_time_source value."""
    return _PUBLISH_SOURCE_MAP.get(raw, "detail" if raw else "")


def parse_datetime(
    value: Any,
    date_format: str | None = None,
    timezone_name: str = "Asia/Shanghai",
    *,
    allow_year_adjust: bool = True,
    reference_year: int | None = None,
    allow_future: bool = False,
) -> dt.datetime | None:
    """Parse common Chinese news date strings into an aware datetime."""
    text = clean_text(value)
    if not text:
        return None

    tz = get_timezone(timezone_name)
    now = dt.datetime.now(tz)

    def _finalize(parsed: dt.datetime) -> dt.datetime | None:
        """Reject (or optionally shift) a future date, regardless of parse path."""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        if parsed > now + dt.timedelta(days=1):
            # 截止时间允许位于未来：项目申报公告的“11月30日”就是未来日期，
            # 不能按新闻发布时间回退到上一年。
            if allow_future:
                return parsed
            if not allow_year_adjust:
                return None
            # 文本里自带年份时不能减一年：列表页若给出 2026-11-14 这类
            # 明确未来日期，减成 2025 会被误判为 old 并永久跳过。
            # 无年份的月日（09-30）才按“去年”回退。
            if has_explicit_year(text):
                LOG.warning("日期 %r 晚于当前时间，保留其显式年份", text)
                return parsed
            try:
                return parsed.replace(year=parsed.year - 1)
            except ValueError:
                return None
        return parsed

    # ISO8601 带显式时区（Z / +08:00 / +0800）必须先处理：下面的通用日期正则
    # 会先命中日期时间部分并把尾部的时区偏移丢掉，再套用站点时区，导致
    # “2026-08-26T10:30:00+08:00”被当作本地墙钟时间而偏移被吞。这里解析出
    # 真正的偏移量并返回带正确 tzinfo 的时间，避免把绝对时刻算错。
    iso_tz = re.search(
        r"(?P<y>20\d{2})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})"
        r"[T ](?P<h>\d{1,2}):(?P<mi>\d{1,2})"
        r"(?::(?P<s>\d{1,2})(?:\.(?P<frac>\d{1,6}))?)?"
        r"(?P<tz>Z|z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)\b",
        text,
    )
    if iso_tz:
        offset = iso_tz.group("tz")
        frac = iso_tz.group("frac") or ""
        micro = int(frac.ljust(6, "0")) if frac else 0
        try:
            if offset in ("Z", "z"):
                parsed_tz: dt.tzinfo = dt.timezone.utc
            else:
                sign = 1 if offset[0] == "+" else -1
                digits = offset[1:].replace(":", "")
                parsed_tz = dt.timezone(
                    sign
                    * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
                )
            return _finalize(dt.datetime(
                int(iso_tz.group("y")),
                int(iso_tz.group("mo")),
                int(iso_tz.group("d")),
                int(iso_tz.group("h")),
                int(iso_tz.group("mi")),
                int(iso_tz.group("s") or 0),
                micro,
                tzinfo=parsed_tz,
            ))
        except ValueError:
            pass

    if "刚刚" in text or text.lower() == "now":
        return now

    relative = re.search(r"(\d+)\s*(分钟|小时|天)前", text)
    if relative:
        number = int(relative.group(1))
        unit = relative.group(2)
        if unit == "分钟":
            return now - dt.timedelta(minutes=number)
        if unit == "小时":
            return now - dt.timedelta(hours=number)
        return now - dt.timedelta(days=number)

    if "昨天" in text:
        return now - dt.timedelta(days=1)
    if "前天" in text:
        return now - dt.timedelta(days=2)

    if date_format:
        try:
            parsed = dt.datetime.strptime(text, date_format)
            # 格式串不含年份（如 %m月%d日）时 strptime 会得到 1900 年；
            # 调用方给了基准年时必须替换，否则截止时间会被误判为早已过期。
            if reference_year is not None and "%Y" not in date_format and "%y" not in date_format:
                parsed = parsed.replace(year=reference_year)
            return _finalize(parsed.replace(tzinfo=tz))
        except ValueError:
            pass

    # Day-first format used by some university sites: 04 2026.09
    day_first = re.search(
        r"(?<!\d)(\d{1,2})\s+(20\d{2})\.(\d{1,2})"
        r"(?:\s+(\d{1,2}):(\d{2}))?",
        text,
    )
    if day_first:
        try:
            parsed = dt.datetime(
                int(day_first.group(2)),
                int(day_first.group(3)),
                int(day_first.group(1)),
                int(day_first.group(4) or 0),
                int(day_first.group(5) or 0),
                tzinfo=tz,
            )
        except ValueError:
            pass
        else:
            return _finalize(parsed)

    # Full year: 2026-08-26 / 2026年8月26日 / 2026-08-26 10:30:00
    full = re.search(
        r"(?<!\d)((?:19|20)\d{2}|\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"
        r"(?:日)?(?:[ T]+(\d{1,2})[:：](\d{1,2})(?::(\d{1,2}))?)?",
        text,
    )
    if full:
        year = int(full.group(1))
        if year < 100:
            year += 2000
        try:
            parsed = dt.datetime(
                year,
                int(full.group(2)),
                int(full.group(3)),
                int(full.group(4) or 0),
                int(full.group(5) or 0),
                int(full.group(6) or 0),
                tzinfo=tz,
            )
        except ValueError:
            return None
        return _finalize(parsed)

    # Month/day only: 08-26 / 8月26日 / 08-26 10:30
    short = re.search(
        r"(?<!\d)(\d{1,2})[-/月.](\d{1,2})(?:日)?"
        r"(?:[ T]+(\d{1,2})[:：](\d{1,2})(?::(\d{1,2}))?)?",
        text,
    )
    if short:
        year = reference_year if reference_year is not None else now.year
        try:
            parsed = dt.datetime(
                year,
                int(short.group(1)),
                int(short.group(2)),
                int(short.group(3) or 0),
                int(short.group(4) or 0),
                int(short.group(5) or 0),
                tzinfo=tz,
            )
        except ValueError:
            return None
        return _finalize(parsed)

    try:
        return _finalize(dt.datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def align_yearless_datetime(
    value: dt.datetime,
    reference: dt.datetime,
    timezone_name: str = "Asia/Shanghai",
) -> dt.datetime | None:
    """用可信基准日期的前后年份补齐无年份日期，月日与时分来自 *value*。"""
    tz = get_timezone(timezone_name)
    # 无 tzinfo 的 datetime 按站点本地墙钟解释，不依赖进程系统时区。
    local_value = value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    local_reference = (
        reference.replace(tzinfo=tz) if reference.tzinfo is None else reference.astimezone(tz)
    )
    # 无年份日期可能是跨年的：列表日期在 1 月初、详情页写“12月28日”，
    # 或列表日期在年末、详情页写次年“01月05日”。因此前后各一年都尝试，
    # 取离基准最近的合法结果，避免把真实发布时间静默换成列表日期。
    candidates: list[tuple[float, dt.datetime]] = []
    for year in (
        local_reference.year - 1,
        local_reference.year,
        local_reference.year + 1,
    ):
        candidate = (
            f"{year:04d}-{local_value.month:02d}-{local_value.day:02d} "
            f"{local_value.hour:02d}:{local_value.minute:02d}:{local_value.second:02d}"
        )
        parsed = parse_datetime(candidate, None, timezone_name, allow_year_adjust=False)
        if parsed is None:
            continue
        distance = abs((parsed - local_reference).total_seconds())
        candidates.append((distance, parsed))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def parse_yearless_around(
    raw: str,
    reference: dt.datetime,
    timezone_name: str = "Asia/Shanghai",
) -> dt.datetime | None:
    """按基准日期前后年份解析无年份月日，取离基准最近的合法结果。

    parse_datetime(..., reference_year=基准年) 遇到“列表日期在 1 月初、
    详情写去年 12 月”时，会先构造出未来日期并返回 None；这里前后各一年都尝试，
    取离基准最近的合法结果，避免详情时刻与来源被列表日期整体替代。
    """
    tz = get_timezone(timezone_name)
    local_reference = (
        reference.replace(tzinfo=tz) if reference.tzinfo is None else reference.astimezone(tz)
    )
    candidates: list[tuple[float, dt.datetime]] = []
    for year in (
        local_reference.year,
        local_reference.year - 1,
        local_reference.year + 1,
    ):
        parsed = parse_datetime(
            raw,
            None,
            timezone_name,
            allow_year_adjust=False,
            reference_year=year,
        )
        if parsed is None:
            continue
        candidates.append((abs((parsed - local_reference).total_seconds()), parsed))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _project_status_closed(value: str) -> bool:
    """列表状态是否明确表示已截止，用于无年份截止时间的年份推断。

    判定忽略内部空白（`已 截止`/全角空格/NBSP），落库的 `project_status` 仍保留原文。
    """
    text = re.sub(r"\s+", "", clean_text(value))
    return any(
        token in text for token in ("已截止", "已结束", "已过期", "报名结束", "已关闭")
    )


def parse_deadline_after(
    raw: str,
    fmt: str | None,
    reference: dt.datetime,
    timezone_name: str = "Asia/Shanghai",
    *,
    allow_past: bool = False,
    not_before: dt.datetime | None = None,
    not_after: dt.datetime | None = None,
) -> dt.datetime | None:
    """无年份截止时间按基准年取最近合法年份：默认不早于 reference，
    allow_past 时不晚于 reference。

    例：2026-12-20 发布“1月15日截止”应解析为 2027-01-15，而不是已过去的
    2026-01-15；有明确年份的文本直接按 parse_datetime 处理。

    allow_past=True 用于列表已明确标注“已截止”的行：截止时间必须已经
    过去，取不晚于 reference 的最近年份，避免把 2026-11 列表里的
    “10月01日 / 已截止”推成 2027-10-01。此时可用 not_before/not_after
    限定可信窗口（通常是列表发布日期到抓取时刻，not_before 缺失时只约束
    上界；仅 allow_past=True 时生效）：区间内没有候选时返回 None，不猜年份
    （落库时 deadline 留空、只保留 deadline_raw 原文）。

    fmt 不含 %Y/%y（如 %m%d）时同样按基准年补齐：strptime 会把缺失年份
    填成 1900，不能直接交给通用解析。
    """
    text = clean_text(raw)
    if not text:
        return None
    # 原文自带年份时年份可信，不参与基准年推断。
    if has_explicit_year(text):
        return parse_datetime(
            text, fmt, timezone_name, allow_year_adjust=False, allow_future=True
        )
    fmt_has_year = bool(fmt and re.search(r"%[Yy]", fmt))
    # 不是“月日”形态、且格式本身含年份（或未配置格式）：按普通解析。
    if not is_yearless_month_day(text) and (not fmt or fmt_has_year):
        return parse_datetime(
            text, fmt, timezone_name, allow_year_adjust=False, allow_future=True
        )
    tz = get_timezone(timezone_name)
    local_reference = (
        reference.replace(tzinfo=tz)
        if reference.tzinfo is None
        else reference.astimezone(tz)
    )
    def _local(value: dt.datetime) -> dt.datetime:
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)

    lower = _local(not_before).date() if (allow_past and not_before is not None) else None
    upper = (
        _local(not_after).date()
        if (allow_past and not_after is not None)
        else local_reference.date()
    )
    candidates: list[dt.datetime] = []
    # 闰日（2 月 29 日）可能隔两年才出现，多试到第三个后续年份；
    # allow_past 还要回看四年，否则“已截止 + 2月29日”在非闰年附近会解析失败。
    # 已截止且给了 not_before/not_after 时，候选年份必须覆盖整个可信区间
    # （发布日期可能距抓取时刻超过三年），否则选不到区间上界附近的年份。
    if allow_past:
        first_year = min(
            local_reference.year - 4, lower.year if lower is not None else local_reference.year
        )
        last_year = max(local_reference.year + 3, upper.year)
        years = range(first_year, last_year + 1)
    else:
        years = range(local_reference.year, local_reference.year + 4)
    for year in years:
        parsed = parse_datetime(
            text,
            fmt,
            timezone_name,
            allow_year_adjust=False,
            reference_year=year,
            allow_future=True,
        )
        if parsed is not None:
            candidates.append(parsed)

    if allow_past:
        if not_before is not None or not_after is not None:
            windowed = [
                candidate
                for candidate in candidates
                if (lower is None or candidate.date() >= lower)
                and (upper is None or candidate.date() <= upper)
            ]
            return max(windowed) if windowed else None
        past = [
            candidate
            for candidate in candidates
            if candidate.date() <= local_reference.date()
        ]
        return max(past) if past else None
    future = [
        candidate
        for candidate in candidates
        if candidate.date() >= local_reference.date()
    ]
    if not future:
        return None
    return min(future)


def format_publish_time(value: dt.datetime, timezone_name: str) -> str:
    """Format an aware datetime as the site local wall clock."""
    tz = get_timezone(timezone_name)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def publish_time_to_utc(publish_time: str, timezone_name: str) -> str:
    """把站点本地墙钟发布时间换算成 UTC 墙钟串（YYYY-MM-DD HH:MM:SS）。

    混时区站点若直接按 publish_time 墙钟字符串比较，边界会错位；统一换算
    为 UTC 后可在 SQL 里做同口径过滤。无法解析或缺时区时返回空串。
    """
    text = str(publish_time or "").strip()
    tz_name = str(timezone_name or "").strip()
    if not text or not tz_name:
        return ""
    try:
        parsed = dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=get_timezone(tz_name))
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns/indexes introduced after the first schema version."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    indexes = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    # 只有“版本号到位、列齐全、性能索引齐全”才跳过迁移：手工改库、降级、
    # 中途异常都可能让 user_version 已置位而结构缺失，此时早退会让后续
    # INSERT/SELECT 报错或让查询退化成全表排序。
    required_indexes = {sql.split("INDEX IF NOT EXISTS ")[1].split(" ")[0]
                        for sql in _PERFORMANCE_INDEX_SQL}
    # article_id 唯一索引与 crawl_jobs 去重索引同样是结构的一部分：缺了它们
    # 早退会让 article_id 失去唯一约束、作业去重失效，因此一并校验。
    required_indexes |= {"idx_articles_article_id", "idx_crawl_jobs_dedupe"}
    if (
        version >= SCHEMA_VERSION
        and {
            "article_id",
            "timezone",
            "raw_content",
            "content_length",
            "publish_time_utc",
            "publish_time_source",
            "publish_time_raw",
            "list_publish_time",
            "title_source",
            "deadline",
            "deadline_raw",
            "project_status",
        } <= columns
        and required_indexes <= indexes
    ):
        return
    if "article_id" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN article_id TEXT")
    if "timezone" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN timezone TEXT")
    # v4：正文保留段落换行。content 仍是 clean_text 归一化后的单行文本，
    # raw_content 存 GNE 原始版本；老库只补列，不自动回填（原文已不可得）。
    if "raw_content" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN raw_content TEXT")
    # v5：列表接口用 content_length 列避免读整段正文 blob；老库补列后回填。
    if "content_length" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN content_length INTEGER")
    # v6：publish_time_utc 存真实时刻（UTC），修复混时区按墙钟过滤的错位。
    if "publish_time_utc" not in columns:
        conn.execute("ALTER TABLE articles ADD COLUMN publish_time_utc TEXT")
    # v7：记录发布时间/标题来源，供站点质量统计与问题排查；老行保持 NULL。
    for column in ("publish_time_source", "publish_time_raw", "list_publish_time", "title_source"):
        if column not in columns:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {column} TEXT")
    # v8：项目申报列表特化，截止时间与申报状态；新闻站点保持 NULL。
    for column in ("deadline", "deadline_raw", "project_status"):
        if column not in columns:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {column} TEXT")

    # 分批回填，避免大库一次性 fetchall 占满内存；仍在同一事务内，保证
    # article_id 全部回填后才会置位 user_version。用 rowid 键集分页而不是
    # 边读游标边改同一张表：后者会让游标在 UPDATE 过程中看到被改动的行，
    # 在不同 SQLite 计划下可能漏读或重复扫描。
    last_rowid = -1
    while True:
        batch = conn.execute(
            "SELECT rowid, source, url FROM articles "
            "WHERE (article_id IS NULL OR article_id = '') AND rowid > ? "
            "ORDER BY rowid LIMIT 2000",
            (last_rowid,),
        ).fetchall()
        if not batch:
            break
        conn.executemany(
            "UPDATE articles SET article_id = ? WHERE rowid = ?",
            [(article_id_for(source, url), rowid) for rowid, source, url in batch],
        )
        last_rowid = batch[-1][0]
    # 回填 content_length（只在列为 NULL 时算一次；正文为 NULL 的行保持 NULL）。
    conn.execute(
        "UPDATE articles SET content_length = length(content) "
        "WHERE content_length IS NULL AND content IS NOT NULL"
    )
    # 回填 publish_time_utc：按每行自己的 timezone 换算，缺 timezone 的行保持
    # NULL（查询侧对 NULL 回退旧的墙钟比较，行为与升级前一致）。
    last_rowid = -1
    while True:
        batch = conn.execute(
            "SELECT rowid, publish_time, timezone FROM articles "
            "WHERE (publish_time_utc IS NULL OR publish_time_utc = '') "
            "AND publish_time IS NOT NULL AND publish_time <> '' "
            "AND timezone IS NOT NULL AND timezone <> '' AND rowid > ? "
            "ORDER BY rowid LIMIT 2000",
            (last_rowid,),
        ).fetchall()
        if not batch:
            break
        updates = []
        for rowid, publish_time, tz_name in batch:
            utc = publish_time_to_utc(publish_time, tz_name)
            if utc:
                updates.append((utc, rowid))
        if updates:
            conn.executemany(
                "UPDATE articles SET publish_time_utc = ? WHERE rowid = ?", updates
            )
        last_rowid = batch[-1][0]
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_article_id "
        "ON articles(article_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_jobs_dedupe "
        "ON crawl_jobs(type, payload, status)"
    )
    # 查询性能索引（v3 引入）：老库升级时补建；新建库 user_version=0 同样走这里。
    for statement in _PERFORMANCE_INDEX_SQL:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL once, tolerating concurrent first-start connections.

    PRAGMA journal_mode 不受 busy_timeout 保护：多个进程同时首次初始化时，
    另一个连接持锁会让该语句直接抛 "database is locked"。已是 WAL 时跳过，
    否则短重试后放行（后续 BEGIN IMMEDIATE 仍负责串行化迁移）。
    """
    try:
        current = conn.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.OperationalError:
        current = ""
    if str(current).lower() == "wal":
        return
    for attempt in range(3):
        try:
            row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        except sqlite3.OperationalError as exc:
            if attempt == 2:
                LOG.warning("切换到 WAL 失败，继续使用当前日志模式：%s", exc)
                return
            time.sleep(0.2)
            continue
        # 不支持的库（如 :memory: 或网络文件系统）不抛异常，只返回原模式，
        # 因此必须校验返回值，否则会静默跑在 delete 模式。
        mode = str(row[0]).lower() if row else ""
        if mode == "wal":
            return
        if attempt == 2:
            LOG.warning("无法切换到 WAL，继续使用日志模式：%s", mode or "unknown")
            return
        time.sleep(0.2)


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=DB_BUSY_TIMEOUT_MS / 1000)
    try:
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
        _ensure_wal(conn)
        conn.executescript(SCHEMA_SQL)
        # 迁移必须串行执行：API 可能同时运行多个 extract 作业，
        # 并发 ALTER TABLE / 回填会互相冲突。BEGIN IMMEDIATE 让其他
        # 连接在 busy_timeout 内等待，进入后再重新读取列状态。
        conn.execute("BEGIN IMMEDIATE")
        try:
            _migrate_schema(conn)
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def resolve_config_path(path: str) -> str:
    """Prefer a private local config when present; the repo default is public."""
    if path == DEFAULT_SOURCES_FILE and Path("sources.local.json").exists():
        return "sources.local.json"
    return path


def _validate_xpath_field(name: object, value: object, field_name: str) -> str:
    """校验手写的 article_title_xpath / article_publish_time_xpath / article_body_xpath。

    - `string(...)` 开头的表达式一律忽略（统一改用元素节点或 /text()）；
    - 非法 XPath 或 count()/boolean()/string-length() 等标量表达式会被忽略；
    - 运行时无效的表达式由各抽取器安全跳过，不会中断整篇抽取。
    元素节点写法（`//h1`）由标题抽取器取 text_content，可拿到完整标题。
    """
    xpath = str(value or "").strip()
    if not xpath:
        return ""
    if xpath.startswith("string(") or xpath.startswith(
        ("count(", "boolean(", "string-length(", "number(")
    ):
        LOG.warning(
            "站点 %r 的 %s 不能写成 %r（请直接写目标元素，如 //h1），已忽略",
            name,
            field_name,
            xpath,
        )
        return ""
    try:
        etree.XPath(xpath)
    except Exception as exc:
        LOG.warning(
            "站点 %r 的 %s 不是合法 XPath（%s），已忽略: %r",
            name,
            field_name,
            exc,
            xpath,
        )
        return ""
    return xpath


def _validate_title_xpath(name: object, value: object) -> str:
    """兼容旧调用：只校验 article_title_xpath。"""
    return _validate_xpath_field(name, value, "article_title_xpath")


def _coerce_bool(value: object, *, default: bool) -> tuple[bool, bool]:
    """把配置里的布尔值归一化，返回 (结果, 是否合法)。

    JSON 里常见的 "false"/"true"/0/1 字符串不应被 bool() 误判为 True。
    """
    if isinstance(value, bool):
        return value, True
    if isinstance(value, int) and value in (0, 1):
        return bool(value), True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True, True
        if normalized in ("false", "0", "no", "off"):
            return False, True
    return default, False


def load_config(path: str) -> dict:
    """读取 JSON 配置并补齐默认值，同时校验各站点的字段 XPath。"""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"找不到配置文件 {path}。请参考 README 创建并填写配置文件。"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = {"sources": data}
    if not isinstance(data, dict):
        # 手写配置顶层误写成字符串/数字时，后续 data.setdefault 会抛
        # AttributeError（不是 OSError/ValueError），调用方捕获不到就崩栈。
        raise ValueError(f"配置文件 {path} 顶层必须是对象或数组，实际是 {type(data).__name__}")
    data.setdefault("sources", [])
    data.setdefault("workers", 16)
    data.setdefault("days", 3)
    data.setdefault("timezone", "Asia/Shanghai")
    data.setdefault("delay", 0.3)
    data.setdefault("date_skew_days", 7)
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for field_name in (
            "article_title_xpath",
            "article_publish_time_xpath",
            "article_body_xpath",
        ):
            if field_name in source:
                source[field_name] = _validate_xpath_field(
                    source.get("name"), source.get(field_name), field_name
                )
        # 项目申报特化：deadline_xpath / project_status_xpath 与标题时间 XPath
        # 走同一套校验（非法或标量表达式直接忽略，不中断整站抓取）。
        for field_name in ("deadline_xpath", "project_status_xpath"):
            if field_name in source:
                source[field_name] = _validate_xpath_field(
                    source.get("name"), source.get(field_name), field_name
                )
        listing_type = str(source.get("listing_type") or "news").strip().lower()
        if listing_type not in ("news", "project_application"):
            LOG.warning(
                "站点 %r 的 listing_type %r 不受支持，按 news 处理",
                source.get("name"),
                source.get("listing_type"),
            )
            listing_type = "news"
        source["listing_type"] = listing_type
        # 项目申报列表本身即有效数据（标题+截止时间+状态），详情页可能要求
        # 登录；默认允许无正文入库，news 站点仍要求正文。
        default_content_required = listing_type != "project_application"
        if "content_required" in source:
            content_required, valid = _coerce_bool(
                source.get("content_required"), default=default_content_required
            )
            if not valid:
                LOG.warning(
                    "站点 %r 的 content_required 不是布尔值，回退为 %s: %r",
                    source.get("name"),
                    default_content_required,
                    source.get("content_required"),
                )
            source["content_required"] = content_required
        else:
            source["content_required"] = default_content_required
        if listing_type == "project_application" and not source.get("date_xpath"):
            # 项目行只在配置了 date_xpath 时才有 publish_time；这里提示一次，
            # 便于排查「项目行 published_at 为空」是否因为漏配发布日期列。
            # 用 debug：API 的 extract 作业每篇都会 load_config，info 会刷屏。
            LOG.debug(
                "站点 %r 是项目申报模式且未配置 date_xpath，"
                "项目行 publish_time 将为空，只能按 deadline 查询",
                source.get("name"),
            )
        if listing_type == "project_application" and not source.get("deadline_xpath"):
            LOG.warning(
                "站点 %r 是项目申报模式但未配置 deadline_xpath，deadline 将为空",
                source.get("name"),
            )
        source.setdefault("date_skew_days", data["date_skew_days"])
        try:
            raw_skew = source["date_skew_days"]
            if isinstance(raw_skew, bool):
                raise ValueError("boolean is not a valid day count")
            source["date_skew_days"] = max(0, min(365, int(raw_skew)))
        except (TypeError, ValueError):
            LOG.warning(
                "站点 %r 的 date_skew_days 不是整数，回退为 7: %r",
                source.get("name"),
                source.get("date_skew_days"),
            )
            source["date_skew_days"] = 7
    return data


def normalize_xpath(xpath: str) -> str:
    """Turn an absolute //xpath into a descendant .//xpath when used on a node."""
    if xpath.startswith("//"):
        return "." + xpath
    return xpath


def xpath_text(node, xpath: str | None) -> str:
    if not xpath:
        return clean_text(node.text_content())
    try:
        values = node.xpath(normalize_xpath(xpath))
    except Exception:
        return clean_text(node.text_content())
    if isinstance(values, str):
        return clean_text(values)
    # count()/number()/string-length() 等表达式返回标量，不能直接迭代。
    if not isinstance(values, list):
        values = [values]
    parts = []
    for value in values:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(getattr(value, "tag", None), str):
            text_content = getattr(value, "text_content", None)
            if callable(text_content):
                parts.append(text_content() or "")
            else:
                parts.append("".join(value.itertext()))
    return clean_text(" ".join(parts))


def xpath_href(node, xpath: str | None) -> str:
    try:
        values = node.xpath(normalize_xpath(xpath or ".//a/@href"))
    except Exception:
        values = []
    if isinstance(values, str):
        return clean_text(values)
    if not isinstance(values, list):
        values = [values]
    for value in values:
        if not isinstance(value, str):
            continue
        href = clean_text(value)
        if href and not href.startswith("javascript:"):
            return href
    return ""


def parse_list_items(html: str, source: dict, base_url: str) -> list[dict]:
    doc = lxml.html.fromstring(html)
    try:
        nodes = doc.xpath(source["item_xpath"])
    except etree.XPathEvalError as exc:
        LOG.error(
            "[%s] item_xpath 非法：%s（%s）",
            source.get("name"),
            source["item_xpath"],
            exc,
        )
        return []
    tz_name = source.get("timezone", "Asia/Shanghai")
    date_format = source.get("date_format")
    deadline_format = source.get("deadline_format") or date_format
    is_project = source.get("listing_type") == "project_application"
    items: list[dict] = []
    seen: set[str] = set()

    for node in nodes:
        # 标题可能来自 HTML 属性（如 node/@title），属性里的字面量 <br> 会被
        # 原样带出，入库前统一清掉，避免标题正文出现标签碎片。
        title = clean_title(xpath_text(node, source.get("title_xpath") or ".//a"))
        href = xpath_href(node, source.get("url_xpath"))
        if not title and not href:
            continue
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        date_xpath = source.get("date_xpath")
        if is_project and not date_xpath:
            # 项目申报列表通常只有「截止时间」没有发布时间，不能把截止时间
            # 当发布日期；未配置 date_xpath 时直接保持 parsed=None。
            date_text = ""
            parsed = None
        elif date_xpath:
            date_text = xpath_text(node, date_xpath)
            parsed = parse_datetime(date_text, date_format, tz_name)
            if parsed is None and not is_project:
                # Some sites put the publish date in the article URL instead,
                # for example https://.../2026/09/05/article.shtml
                # 项目行不认 URL 日期：date_xpath 取到“待定”时回退 URL 会把
                # 链接里的 /2025/11/01/ 当成列表发布日期写进 publish_time，
                # 并在后续刷新中覆盖已有的有效列表日期。
                parsed = parse_datetime(url, None, tz_name)
        else:
            # 未配置 date_xpath 时，URL 中的日期比整节点文本更权威；
            # 直接用整节点文本会把标题里的日期（如“2024年1月1日起施行”）
            # 误当成发布日，故先尝试 URL，再退到“剔除标题后的节点文本”。
            date_text = ""
            parsed = parse_datetime(url, None, tz_name)
            if parsed is None:
                node_text = clean_text(node.text_content())
                title_text = title or xpath_text(
                    node, source.get("title_xpath") or ".//a"
                )
                if title_text:
                    node_text = node_text.replace(title_text, " ", 1)
                date_text = node_text
                parsed = parse_datetime(date_text, date_format, tz_name)
        project_status = ""
        status_xpath = source.get("project_status_xpath")
        if status_xpath:
            project_status = xpath_text(node, status_xpath)
        deadline_xpath = source.get("deadline_xpath")
        deadline_text = ""
        deadline_dt = None
        if deadline_xpath:
            deadline_text = xpath_text(node, deadline_xpath)
            # 截止时间可以晚于当前时间；无年份的“11月30日”必须保留今年，
            # 不能走发布时间“未来回退上一年”的逻辑。跨年场景（2026-12-20
            # 发布“1月15日截止”）则按列表日期/当前时间取不早于基准的年份。
            # 列表已明确“已截止”时截止时间必须已经过去，不能推成下一年。
            status_closed = _project_status_closed(project_status)
            now = dt.datetime.now(get_timezone(tz_name))
            reference = parsed if parsed is not None else now
            deadline_dt = parse_deadline_after(
                deadline_text,
                deadline_format,
                reference,
                tz_name,
                allow_past=status_closed,
                # 已截止：截止时间必须落在公告发布之后、抓取时刻之前；区间内
                # 没有候选时保留原文（例如 2025-12-20 发布的“1月15日”不能
                # 解析成 2025-01-15）。
                not_before=parsed if status_closed else None,
                not_after=now if status_closed else None,
            )
        items.append(
            {
                "title": title,
                "url": url,
                "date_text": date_text,
                "parsed": parsed,
                "deadline_text": deadline_text,
                "deadline": deadline_dt,
                "project_status": project_status,
            }
        )
    return items


def fetch_list_html(
    url: str,
    render: bool,
    *,
    url_validator: Callable[[str], None] | None = None,
) -> str | None:
    if render:
        return fetch_html_with_browser(url, url_validator=url_validator)
    try:
        return fetch_html(url, url_validator=url_validator)
    except urllib.error.HTTPError as exc:
        # 与文章页同一套规则：404/410/500 渲染后仍是同样错误，不值得再启浏览器。
        if exc.code not in BROWSER_FALLBACK_HTTP_CODES:
            LOG.warning("直接抓取列表失败 %s (HTTP %s)，不回退浏览器。", url, exc.code)
            return None
        LOG.warning("直接抓取列表被拒绝 %s (HTTP %s)，尝试浏览器。", url, exc.code)
        return fetch_html_with_browser(url, url_validator=url_validator)
    except Exception as exc:
        LOG.warning("直接抓取列表失败 %s (%s)，尝试浏览器。", url, exc)
        return fetch_html_with_browser(url, url_validator=url_validator)


def _url_origin(value: str) -> tuple[str, str, int] | None:
    """Return the (scheme, host, port) origin of a configured list URL."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().strip("[]")
    if scheme not in ("http", "https") or not host:
        return None
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return scheme, host, port


_TRUSTED_ENDPOINT_RETRY_SECONDS = 60.0


def _pagination_url_validator(source: dict) -> Callable[[str], None]:
    """Build an SSRF guard for a site's list pages and browser subrequests.

    配置里的 list_url/list_urls 是唯一可信目标，且必须 scheme + host + port
    完全一致才允许内网地址。其他目标必须是公网地址，并且只允许 http 80 /
    https 443。这样攻击者即使改下一页链接，也不能借爬虫访问配置主机的其他
    端口；同端口无法按路径区分，配置主机本身视为可信。连接层解析出的可信
    主机 IP 只在与该主机的配置端口组合时放行，不构成独立的 URL 白名单。
    """
    configured: list[Any] = list(source.get("list_urls") or [])
    if source.get("list_url"):
        configured.append(source["list_url"])
    trusted_origins: set[tuple[str, str, int]] = set()
    trusted_ports: dict[str, set[int]] = {}
    for raw in configured:
        origin = _url_origin(str(raw))
        if origin is None:
            continue
        _scheme, host, port = origin
        trusted_origins.add(origin)
        trusted_ports.setdefault(host, set()).add(port)

    trusted_endpoints: set[tuple[str, int]] = set()
    resolved = False
    warned_hosts: set[str] = set()
    retry_after: dict[str, float] = {}

    def _resolve_trusted_endpoints() -> set[tuple[str, int]]:
        nonlocal resolved
        if resolved:
            return trusted_endpoints
        failed = False
        now = time.monotonic()
        for host, ports in trusted_ports.items():
            if retry_after.get(host, 0.0) > now:
                # 解析失败的 host 在冷却期内跳过，避免每个连接都重试并刷日志。
                failed = True
                continue
            try:
                infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except OSError as exc:
                # 解析失败不写入缓存，冷却期后重试；否则一次 DNS 抖动会让
                # 本次抓取内所有连接层校验都拿不到可信端点。
                if host not in warned_hosts:
                    LOG.warning("无法解析可信站点主机 %r 的地址: %s", host, exc)
                    warned_hosts.add(host)
                else:
                    LOG.debug("仍未解析可信站点主机 %r: %s", host, exc)
                failed = True
                retry_after[host] = now + _TRUSTED_ENDPOINT_RETRY_SECONDS
                continue
            retry_after.pop(host, None)
            for info in infos:
                address = str(info[4][0]).split("%", 1)[0]
                try:
                    ip = ipaddress.ip_address(address)
                except ValueError:
                    continue
                if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                    ip = ip.ipv4_mapped
                for port in ports:
                    trusted_endpoints.add((str(ip), port))
        if not failed:
            resolved = True
        return trusted_endpoints

    def _validator(target: str) -> None:
        # allow_private=True 只做格式/凭据/控制字符校验；地址策略在下面自行判断。
        ensure_url_allowed(target, allow_private=True)
        try:
            parts = urlsplit(target)
        except ValueError as exc:
            raise UnsafeUrlError(f"URL 格式不合法: {exc}") from exc
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower().strip("[]")
        if not host:
            raise UnsafeUrlError("URL 缺少主机名")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeUrlError(f"URL 端口不合法: {exc}") from exc
        if (scheme, host, port) in trusted_origins:
            return
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            # 连接层会把可信主机解析出的 IP 以字面量 URL 再校验一次；
            # 只有与该主机配置端口组合时放行，不构成独立的 URL 白名单。
            if (str(address), port) in _resolve_trusted_endpoints():
                return
        if port not in (80, 443):
            raise UnsafeUrlError(f"非配置主机只允许 80/443 端口: {target}")
        if address is not None:
            if address.is_global:
                return
            raise UnsafeUrlError(f"拒绝访问非配置主机的内网地址: {target}")
        ensure_url_allowed(target, allow_private=False)

    return _validator


def _pagination_int(
    raw: dict[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool):
        LOG.warning("pagination.%s 不能是布尔值，回退为 %d", name, default)
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        LOG.warning("pagination.%s 不是整数（%r），回退为 %d", name, value, default)
        return default
    if parsed < minimum or parsed > maximum:
        LOG.warning(
            "pagination.%s 超出范围 %d..%d（%r），回退为 %d",
            name,
            minimum,
            maximum,
            value,
            default,
        )
        return default
    return parsed


def _pagination_bool(raw: dict[str, Any], name: str, default: bool) -> bool:
    value = raw.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    LOG.warning("pagination.%s 不是布尔值（%r），回退为 %s", name, value, default)
    return default


def _pagination_settings(source: dict) -> dict[str, Any]:
    """返回归一化后的分页配置。

    兼容旧配置：未配置 pagination 时保持 list_urls/list_url 的原有行为。
    """
    defaults: dict[str, Any] = {
        "mode": "none",
        "url_template": "",
        "last_url_xpath": "",
        "next_url_xpath": "",
        "total_pages_regex": r"(?:index_|page[=/])(\d+)",
        "start": 1,
        "max_pages": 50,
        "stop_when_older_than_cutoff": True,
        "stop_after_known_pages": 0,
    }
    raw = source.get("pagination")
    if raw is None or source.get("list_urls"):
        # list_urls 是固定地址列表，pagination 配置不生效；提前返回避免无意义告警。
        return defaults
    if not isinstance(raw, dict):
        LOG.warning("站点 %r 的 pagination 不是对象，已忽略", source.get("name"))
        return defaults

    mode = str(raw.get("mode") or "none").strip().lower()
    if mode not in {"auto", "next", "template", "none"}:
        LOG.warning(
            "站点 %r 的 pagination.mode=%r 非法（可选 auto/next/template/none），已回退为 none",
            source.get("name"),
            raw.get("mode"),
        )
        mode = "none"
    defaults["mode"] = mode
    defaults["url_template"] = str(raw.get("url_template") or "").strip()
    if defaults["url_template"] and "{page}" not in defaults["url_template"]:
        LOG.warning(
            "站点 %r 的 pagination.url_template 缺少 {page} 占位符，已忽略：%r",
            source.get("name"),
            defaults["url_template"],
        )
        defaults["url_template"] = ""
        if mode == "template":
            defaults["mode"] = "none"
    defaults["last_url_xpath"] = str(raw.get("last_url_xpath") or "").strip()
    defaults["next_url_xpath"] = str(raw.get("next_url_xpath") or "").strip()
    defaults["total_pages_regex"] = str(
        raw.get("total_pages_regex") or defaults["total_pages_regex"]
    ).strip()
    defaults["start"] = _pagination_int(raw, "start", 1, 1, 1000)
    defaults["max_pages"] = _pagination_int(raw, "max_pages", 50, 1, 500)
    defaults["stop_when_older_than_cutoff"] = _pagination_bool(
        raw, "stop_when_older_than_cutoff", True
    )
    defaults["stop_after_known_pages"] = _pagination_int(
        raw, "stop_after_known_pages", 0, 0, 100
    )
    return defaults


def _max_page_number(text: str, pattern: str) -> int | None:
    """从 HTML/URL 文本中用正则提取最大的页码。"""
    if not text or not pattern:
        return None
    try:
        matches = re.findall(pattern, text)
    except re.error as exc:
        LOG.warning("total_pages_regex 非法：%s（%s）", pattern, exc)
        return None
    numbers: list[int] = []
    for match in matches:
        value = match[0] if isinstance(match, tuple) and match else match
        if isinstance(value, str) and value.isdigit():
            numbers.append(int(value))
    return max(numbers) if numbers else None


def _max_page_from_html(html: str, source: dict, settings: dict[str, Any]) -> int | None:
    pattern = str(settings["total_pages_regex"])
    doc = lxml.html.fromstring(html)
    last_xpath = str(settings["last_url_xpath"] or "")
    if last_xpath:
        try:
            values = doc.xpath(last_xpath)
        except Exception as exc:
            LOG.warning("站点 %r 的 last_url_xpath 无效：%s", source.get("name"), exc)
            values = []
        if not isinstance(values, list):
            values = [values]
        numbers: list[int] = []
        for value in values:
            candidates: list[str] = []
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(getattr(value, "tag", None), str):
                for attr in ("href", "onclick", "data-page", "value"):
                    attr_value = value.get(attr)
                    if attr_value:
                        candidates.append(str(attr_value))
                candidates.append(value.text_content() or "")
            else:
                continue
            for candidate in candidates:
                text = candidate.strip()
                if text.isdigit():
                    numbers.append(int(text))
                    continue
                number = _max_page_number(candidate, pattern)
                if number is not None:
                    numbers.append(number)
        if numbers:
            return max(numbers)
    return _max_page_number(html, pattern)


def _template_page_urls(
    html: str, page_url: str, source: dict, settings: dict[str, Any]
) -> list[str]:
    template = str(settings["url_template"] or "")
    if not template:
        return []
    max_pages = int(settings["max_pages"])
    detected = _max_page_from_html(html, source, settings)
    start = int(settings["start"])
    # max_pages 是包含 list_url 在内的总页数：后续页最多 max_pages-1 个。
    # 未探测到末页时以 max_pages 为总页数上限；探测到末页时再受末页页码限制。
    page_limit = detected if detected is not None else start + max_pages
    urls: list[str] = []
    page = start
    while len(urls) < max_pages - 1 and page <= page_limit:
        try:
            url = template.format(page=page)
        except (KeyError, IndexError, ValueError) as exc:
            LOG.warning(
                "站点 %r 的 url_template 无法格式化（%s）：%r",
                source.get("name"),
                exc,
                template,
            )
            return []
        if url != page_url:
            urls.append(url)
        page += 1
    return urls


def _usable_href(value: Any) -> str:
    """返回可用链接；空锚点、# 片段和 javascript: 链接都视为无效。"""
    href = str(value or "").strip()
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return ""
    return href


def _onclick_target(value: str) -> str:
    """从 onclick 属性文本中取出 location.href=... 的目标地址。"""
    match = re.search(
        r"(?:window\.)?location(?:\.href)?\s*=\s*"
        r"(?:encodeURI\()?['\"]([^'\"]+)['\"]",
        str(value or ""),
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _find_next_page_url(html: str, page_url: str, source: dict, settings: dict[str, Any]) -> str:
    doc = lxml.html.fromstring(html)
    next_xpath = str(settings["next_url_xpath"] or "")
    if next_xpath:
        try:
            values = doc.xpath(next_xpath)
        except Exception as exc:
            LOG.warning("站点 %r 的 next_url_xpath 无效：%s", source.get("name"), exc)
            values = []
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if isinstance(value, str):
                text = value.strip()
                onclick_target = _onclick_target(text)
                if onclick_target:
                    return urljoin(page_url, onclick_target)
                href = _usable_href(text)
                if href and any(token in href for token in ("/", "?", ".html")):
                    return urljoin(page_url, href)
                continue
            if not isinstance(getattr(value, "tag", None), str):
                continue
            href = _usable_href(value.get("href"))
            if href:
                return urljoin(page_url, href)
            onclick_target = _onclick_target(str(value.get("onclick") or ""))
            if onclick_target:
                return urljoin(page_url, onclick_target)
            text = clean_text(value.text_content())
            onclick_target = _onclick_target(text)
            if onclick_target:
                return urljoin(page_url, onclick_target)
            href = _usable_href(text)
            if href and any(token in href for token in ("/", "?", ".html")):
                return urljoin(page_url, href)

    for anchor in doc.xpath("//a"):
        rel = str(anchor.get("rel") or "").lower()
        text = clean_text(anchor.text_content())
        lower_text = text.lower()
        if "next" not in rel and not any(
            token in lower_text for token in ("下一页", "下页", "后一页", "next")
        ):
            continue
        href = _usable_href(anchor.get("href"))
        if href:
            return urljoin(page_url, href)
        onclick_target = _onclick_target(str(anchor.get("onclick") or ""))
        if onclick_target:
            return urljoin(page_url, onclick_target)
    return ""


def collect_list_entries_detailed(
    source: dict,
    *,
    cutoff: dt.datetime | None = None,
    known_urls: set[str] | None = None,
) -> dict[str, Any]:
    """抓取列表页并返回条目、页数与停止原因。

    pagination.mode:
    - none: 只抓 list_urls/list_url（旧行为）；
    - template: 抓 list_url 后按 url_template 生成后续页；
    - next: 按 next_url_xpath 或“下一页”链接跟随；
    - auto: 有 url_template 时按模板，否则按下一页链接。
    """
    settings = _pagination_settings(source)
    fixed_urls = [str(url) for url in (source.get("list_urls") or [source["list_url"]])]
    fixed_mode = settings["mode"] == "none" or bool(source.get("list_urls"))
    is_project = source.get("listing_type") == "project_application"
    pending = list(fixed_urls)
    all_items: list[dict] = []
    seen_urls: set[str] = set()
    visited_pages: set[str] = set()
    page_urls: list[str] = []
    pages = 0
    known_streak = 0
    fixed_failed = False
    fixed_empty = False
    stop_reason = "done"
    render = bool(source.get("render"))
    delay = float(source.get("delay", 0))
    page_validator = _pagination_url_validator(source)

    while pending:
        page_url = pending.pop(0)
        if page_url in visited_pages:
            continue
        if not fixed_mode and pages >= int(settings["max_pages"]):
            stop_reason = "max_pages"
            break
        visited_pages.add(page_url)
        html = fetch_list_html(page_url, render, url_validator=page_validator)
        if not html:
            if fixed_mode:
                fixed_failed = True
                LOG.warning("固定列表页抓取失败，继续下一页：%s", page_url)
                continue
            stop_reason = "fetch_failed"
            break
        items = parse_list_items(html, source, page_url)
        if not items and not render:
            LOG.info("列表解析为空，尝试 Chrome 渲染 %s", page_url)
            rendered = fetch_html_with_browser(page_url, url_validator=page_validator)
            if rendered:
                # 后续找下一页/模板探测必须基于渲染后的 DOM。
                html = rendered
                items = parse_list_items(html, source, page_url)
        pages += 1
        page_urls.append(page_url)
        if not items:
            if fixed_mode:
                fixed_empty = True
                LOG.warning("固定列表页没有解析到条目，继续下一页：%s", page_url)
                continue
            stop_reason = "empty"
            break
        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_items.append(item)

        parsed_dates = [item["parsed"] for item in items if item.get("parsed")]
        # 项目申报页的 parsed 是公告发布日期，不是截止时间；整页公告都
        # 发布于窗口之前并不代表后面的页没有仍在受理的项目，禁止据此早停。
        if (
            not fixed_mode
            and not is_project
            and cutoff is not None
            and settings["stop_when_older_than_cutoff"]
            and len(parsed_dates) == len(items)
            and max(parsed_dates) < cutoff
        ):
            stop_reason = "cutoff"
            break

        if (
            not fixed_mode
            and known_urls is not None
            and int(settings["stop_after_known_pages"]) > 0
        ):
            page_known = all(item["url"] in known_urls for item in items)
            known_streak = known_streak + 1 if page_known else 0
            if known_streak >= int(settings["stop_after_known_pages"]):
                stop_reason = "known_pages"
                break

        if not fixed_mode and settings["mode"] in {"template", "auto"} and settings["url_template"]:
            if pages == 1:
                pending.extend(_template_page_urls(html, page_url, source, settings))
        elif not fixed_mode and settings["mode"] in {"next", "auto"}:
            next_url = _find_next_page_url(html, page_url, source, settings)
            if next_url and next_url not in visited_pages:
                pending.append(next_url)
            else:
                stop_reason = "no_next"
        time.sleep(delay)

    if fixed_mode and not all_items:
        if fixed_failed:
            stop_reason = "fetch_failed"
        elif fixed_empty:
            stop_reason = "empty"
    return {
        "items": all_items,
        "pages": pages,
        "page_urls": page_urls,
        "stop_reason": stop_reason,
    }


def collect_list_entries(
    source: dict,
    *,
    cutoff: dt.datetime | None = None,
    known_urls: set[str] | None = None,
) -> list[dict]:
    """兼容旧调用：只返回条目列表。"""
    return collect_list_entries_detailed(
        source, cutoff=cutoff, known_urls=known_urls
    )["items"]


def fetch_article_html(url: str) -> tuple[str, bool] | None:
    """Fetch an article, returning (html, already_rendered).

    already_rendered tells extract_article whether the HTML came from the
    browser fallback, so it must not launch Chromium a second time just because
    GNE still considers the content short or empty.
    """
    try:
        return fetch_html(url), False
    except urllib.error.HTTPError as exc:
        # 404/410/500 之类渲染后仍然是同样的错误，回退只会白等一次浏览器
        # 启动（实测单条 1.84s）；只有“被拒绝”的错误才值得换浏览器再试。
        if exc.code not in BROWSER_FALLBACK_HTTP_CODES:
            LOG.warning("直接抓取文章失败 %s (HTTP %s)，不回退浏览器。", url, exc.code)
            return None
        LOG.warning("直接抓取文章被拒绝 %s (HTTP %s)，尝试浏览器。", url, exc.code)
        rendered = fetch_html_with_browser(url)
        return (rendered, True) if rendered else None
    except Exception as exc:
        LOG.warning("直接抓取文章失败 %s (%s)，尝试浏览器。", url, exc)
        rendered = fetch_html_with_browser(url)
        return (rendered, True) if rendered else None


def extract_article(
    html: str,
    url: str,
    *,
    already_rendered: bool = False,
    title_xpath: str = "",
    publish_time_xpath: str = "",
    body_xpath: str = "",
) -> dict | None:
    """用已抓取的 HTML 抽取一篇文章（与 url_extract.extract_article 共用实现）。

    历史原因这里曾维护一份独立的抽取/渲染回退逻辑，与 url_extract.py 的版本
    逐渐分叉（阈值、图片补全、视频判定不一致）。现在统一委托给
    url_extract.extract_from_html，避免两处行为漂移。already_rendered=True
    表示 HTML 已由浏览器渲染过，抽取层不得再次启动 Chromium。
    """
    extractor = GeneralNewsExtractor()
    return extract_from_html(
        html,
        url,
        extractor,
        allow_browser=True,
        already_rendered=already_rendered,
        title_xpath=title_xpath,
        publish_time_xpath=publish_time_xpath,
        body_xpath=body_xpath,
    )


def save_article(
    conn: sqlite3.Connection,
    source: str,
    url: str,
    *,
    list_title: str = "",
    title: str = "",
    publish_time: str = "",
    publish_time_source: str = "",
    publish_time_raw: str = "",
    list_publish_time: str = "",
    title_source: str = "",
    deadline: str = "",
    deadline_raw: str = "",
    project_status: str = "",
    timezone: str = "",
    author: str = "",
    content: str = "",
    raw_content: str = "",
    images: list[str] | None = None,
    status: str,
    error: str = "",
) -> str:
    """Insert or update one article with partial-update semantics.

    空值表示"这次没有拿到该字段"，不会覆盖库中已有内容：重新抓取失败、
    文章被判过期或正文抽取失败时只更新 status/error，已经成功抓到的
    正文、标题、发布时间不会被清空。状态始终按本次结果覆盖。

    images 单独区分三态：None=本次未提供（保留旧值）；[]=明确无图（清空）；
    非空列表=覆盖。旧实现把 [] 也当成"未提供"，导致图片一旦入库就无法清空。
    抓取流程用 normalize_images_for_update 把"抽取不到图"归一为 None，
    只有调用方显式传 [] 才清空，避免成功重抓因抽取失败抹掉历史图片。
    """
    article_id = article_id_for(source, url)
    # 无法换算（缺 timezone / 解析失败）时写 NULL 而不是空串：查询侧对 NULL
    # 回退旧的 wall-clock 比较，空串会让这类行被日期过滤漏掉。
    publish_time_utc = publish_time_to_utc(publish_time, timezone) or None
    images_json = (
        json.dumps(images, ensure_ascii=False) if images is not None else None
    )
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO articles (
            article_id, source, url, list_title, title, publish_time,
            publish_time_utc, publish_time_source, publish_time_raw,
            list_publish_time, title_source, deadline, deadline_raw,
            project_status, timezone, author, content,
            content_length, raw_content, images, status, error,
            first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, url) DO UPDATE SET
            article_id = excluded.article_id,
            list_title = COALESCE(NULLIF(excluded.list_title, ''), articles.list_title),
            title = COALESCE(NULLIF(excluded.title, ''), articles.title),
            publish_time = COALESCE(NULLIF(excluded.publish_time, ''), articles.publish_time),
            publish_time_utc = CASE
                WHEN excluded.publish_time <> '' THEN excluded.publish_time_utc
                ELSE articles.publish_time_utc
            END,
            -- 来源必须与本次写入的时间绑定：本次没拿到时间（unknown_date）
            -- 时不能改来源，否则旧时间会挂着新来源。
            publish_time_source = CASE
                WHEN excluded.publish_time <> ''
                THEN COALESCE(NULLIF(excluded.publish_time_source, ''), articles.publish_time_source)
                ELSE articles.publish_time_source
            END,
            -- 原始时间必须与本次写入的 publish_time 同步：回退到列表日期时
            -- 要显式清掉旧详情原文，否则会出现 source=list 却挂详情原文。
            publish_time_raw = CASE
                WHEN excluded.publish_time <> '' THEN excluded.publish_time_raw
                ELSE articles.publish_time_raw
            END,
            list_publish_time = COALESCE(NULLIF(excluded.list_publish_time, ''), articles.list_publish_time),
            title_source = COALESCE(NULLIF(excluded.title_source, ''), articles.title_source),
            -- 项目申报字段：本次给出了新的截止原文时，deadline 必须与它同步：
            -- 解析失败（如“常年受理”）要清空旧解析值，否则会继续用过期截止
            -- 时间过滤该行；本次没给原文才保留旧值。
            deadline = CASE
                WHEN excluded.deadline_raw <> '' THEN excluded.deadline
                ELSE COALESCE(NULLIF(excluded.deadline, ''), articles.deadline)
            END,
            deadline_raw = COALESCE(NULLIF(excluded.deadline_raw, ''), articles.deadline_raw),
            project_status = COALESCE(NULLIF(excluded.project_status, ''), articles.project_status),
            timezone = COALESCE(NULLIF(excluded.timezone, ''), articles.timezone),
            author = COALESCE(NULLIF(excluded.author, ''), articles.author),
            content = COALESCE(NULLIF(excluded.content, ''), articles.content),
            content_length = CASE WHEN excluded.content <> ''
                THEN length(excluded.content) ELSE articles.content_length END,
            raw_content = COALESCE(NULLIF(excluded.raw_content, ''), articles.raw_content),
            images = CASE WHEN excluded.images IS NULL
                THEN articles.images ELSE excluded.images END,
            status = excluded.status,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            article_id,
            source,
            url,
            list_title,
            title,
            publish_time,
            publish_time_utc,
            publish_time_source,
            publish_time_raw,
            list_publish_time,
            title_source,
            deadline,
            deadline_raw,
            project_status,
            timezone,
            author,
            content,
            len(content),
            raw_content,
            images_json,
            status,
            error,
            stamp,
            stamp,
        ),
    )
    conn.commit()
    return article_id


def should_skip_stored(
    conn: sqlite3.Connection,
    source: str,
    url: str,
    cutoff: dt.datetime,
    timezone_name: str,
) -> bool:
    """已入库的条目是否可以跳过。

    ok 是“已成功抓到且在当前窗口内”，永远跳过。
    old 是“当时确认早于窗口”；判过期时同时写了当时的发布时间，因此只要它
    仍早于当前窗口，结论不变。放宽 --days/--since 让窗口前移后重新抓取，
    避免历史数据只能靠 --force 全量重抓。

    时区优先用行内记录的那一个：站点 timezone 配置被修正后，旧行的墙钟是
    按旧时区写入的，用当前配置重解析会把边界条目判错。

    publish_time 为空或格式无法解析（例如旧版写入的中文日期）时返回 False，
    也就是重抓一次：宁可多抓一次，也不要让一批条目永远停留在无法判定的状态。
    """
    row = conn.execute(
        "SELECT status, publish_time, timezone, deadline FROM articles "
        "WHERE source = ? AND url = ?",
        (source, url),
    ).fetchone()
    if row is None:
        return False
    if row[0] == "ok":
        return True
    if row[0] != "old":
        return False
    stored_tz = (row[2] or "").strip() or timezone_name
    stored_date = parse_datetime(row[1] or "", None, stored_tz)
    if stored_date is None and row[3]:
        # 没有 publish_time 时改用 deadline 判定：项目行未配置 date_xpath 时
        # 只有 deadline 可用。注意调用顺序——_process_project_entry 先调
        # _refresh_project_listing（它已按 deadline 算好 status），这里只处理
        # 状态不是 ok/old、或刷新未覆盖到的行；两者结论可能不一致（发布在窗口
        # 外而截止在窗口内），以 deadline 为准更符合项目申报语义。
        stored_date = parse_datetime(row[3] or "", None, stored_tz)
    return stored_date is not None and stored_date < cutoff


def _refresh_project_listing(
    conn: sqlite3.Connection,
    source_name: str,
    url: str,
    title: str,
    deadline_text: str,
    deadline_dt: dt.datetime | None,
    deadline_raw: str,
    project_status: str,
    cutoff: dt.datetime,
    timezone_name: str,
    content_required: bool,
    publish_text: str = "",
    publish_raw: str = "",
    has_date_xpath: bool = False,
) -> str | None:
    """刷新已入库项目行的列表字段与窗口状态，不抓详情。

    返回 None 表示该行不存在或不是 ok/old，交由后续正常流程处理；
    返回 "skip" 表示列表字段与状态都没有变化；返回 "ok"/"old" 表示已刷新；
    返回 "retry" 表示仍需继续抓正文：列表状态从 old 恢复为 ok，或
    content_required 从 false 改成 true 后已入库的 ok 行正文仍缺失。
    """
    row = conn.execute(
        "SELECT status, title, deadline, deadline_raw, project_status, "
        "publish_time, publish_time_utc, content_length, title_source, "
        "publish_time_source, publish_time_raw, list_publish_time, timezone "
        "FROM articles WHERE source = ? AND url = ?",
        (source_name, url),
    ).fetchone()
    if row is None or row[0] not in ("ok", "old"):
        return None
    old_status = str(row[0])
    old_title = str(row[1] or "")
    old_deadline = str(row[2] or "")
    old_raw = str(row[3] or "")
    old_project_status = str(row[4] or "")
    # 只认 publish_time：早期空串兼容写法会把 UTC 墙钟当本地墙钟（差 8 小时，
    # 跨日边界会错一天），这里不再回落到 publish_time_utc。
    old_publish = str(row[5] or "")
    # 旧 UTC 墙钟只参与「值是否变化」的比较，不直接写库：新的 utc 一律由
    # new_publish 重新换算，避免沿用与本地墙钟不再匹配的旧换算结果。
    old_publish_utc = str(row[6] or "")
    old_content_length = int(row[7] or 0)
    old_title_source = str(row[8] or "")
    old_publish_source = str(row[9] or "")
    old_publish_raw = str(row[10] or "")
    old_list_publish = str(row[11] or "")
    # 行内记录的时区是旧墙钟的解释口径：站点 timezone 配置被修正后，沿用旧
    # 墙钟时必须按它换算 UTC，否则同一串墙钟会被当成新时区的本地时间（差 8
    # 小时以上），API 展示与 published_from/published_to 过滤都会错位。
    old_timezone = str(row[12] or "")
    # content_required 从 false 改成 true 后，之前以“列表字段完整”入库的 ok 行
    # 正文仍为空；必须按正文要求重新抓详情，不能因为列表字段没变就 skip。
    content_missing = content_required and old_content_length < MIN_CONTENT_LENGTH
    new_title = title or old_title
    # 项目行的 publish_time 只来自列表发布日期（date_xpath），与 deadline 无关：
    # 配了 date_xpath 且本次解析成功才写；解析失败保留旧值，避免列表页临时
    # 抖动抹掉已有日期；完全没配 date_xpath 的站点清空，防止从新闻模式切过来
    # 的历史发布时间把项目行混进 published_from/published_to 的结果里。
    keep_legacy_publish = False
    if has_date_xpath:
        new_publish = publish_text or old_publish
        if publish_text:
            new_publish_source = "list"
            new_publish_raw = publish_raw
            new_list_publish = publish_text
        elif old_publish:
            # 本次列表日期解析失败而沿用旧值：旧值可能来自新闻模式的详情日期
            # （source=xpath 等），语义上不是列表发布日期。保留是为了不丢时间，
            # 但不会自动改写成 list；要清空需临时移除 date_xpath 重抓。
            new_publish_source = old_publish_source
            new_publish_raw = old_publish_raw
            new_list_publish = old_list_publish
            keep_legacy_publish = old_publish_source != "list"
        else:
            # 没有任何可沿用的本地墙钟：来源/原文/列表日期一并清空，避免出现
            # 「有 list_publish_time 却没有 publish_time」的不一致状态。
            new_publish_source = ""
            new_publish_raw = ""
            new_list_publish = ""
    else:
        new_publish = ""
        new_publish_source = ""
        new_publish_raw = ""
        new_list_publish = ""
    # 接受本次列表日期时按站点当前时区换算，并把它同步写入行内 timezone，
    # 让墙钟与 UTC 换算口径始终自洽；沿用旧墙钟时用行内记录的时区，配置改过
    # 也不会把旧墙钟按新时区重算。
    # 行内 timezone 为空表示写入时的口径未知（v6 之前的历史行迁移不会回填）：
    # 已有 publish_time_utc 就原样沿用，没有才按站点配置换算，且都不把这个猜测
    # 回写进行内——否则「未知」会被固化成一个确定的时区，之后每轮都 skip，
    # 换算错误再也无法纠正（站点 timezone 配置被修正过的历史行就是这条路径）。
    tz_guessed = False
    tz_config_missing = False
    if publish_text:
        # 站点配置的 timezone 缺失（写成空串）时保留行内已知口径：否则
        # publish_time_to_utc 拿不到时区会返回空串，行内 timezone 也会被清成 NULL，
        # 「已知」降级成「未知」，published_from/published_to 只能退化成墙钟比较。
        effective_timezone = timezone_name or old_timezone
        new_publish_utc = publish_time_to_utc(new_publish, effective_timezone) or ""
        new_timezone = effective_timezone
        if not timezone_name:
            tz_config_missing = True
    elif new_publish:
        if old_timezone:
            new_publish_utc = publish_time_to_utc(new_publish, old_timezone) or ""
            new_timezone = old_timezone
        elif old_publish_utc:
            new_publish_utc = old_publish_utc
            new_timezone = ""
        else:
            # 猜出来的 utc 下一轮会成为「行内时区缺失但已有 utc」分支的可信旧值被
            # 继续沿用，站点配置日后修正也不会自动重算，只能 --force；宁可有值可
            # 过滤，也不保持 NULL。
            new_publish_utc = publish_time_to_utc(new_publish, timezone_name) or ""
            new_timezone = ""
            tz_guessed = True
    else:
        # 没有本地墙钟的行不必补一个时区，否则排查时会误以为它带可信发布时间。
        new_publish_utc = ""
        new_timezone = old_timezone
    # 本次没有给出截止原文时保留旧解析值；给出了就与原文同步，解析失败
    # 意味着旧截止时间已经失效，必须清空而不是继续沿用。
    new_deadline = deadline_text if deadline_raw else old_deadline
    new_raw = deadline_raw if deadline_raw else old_raw
    new_project_status = project_status or old_project_status
    new_status = old_status
    if deadline_raw:
        if deadline_text and deadline_dt is not None:
            new_status = "old" if deadline_dt < cutoff else "ok"
        elif old_status == "old":
            # 原来按 deadline 判 old，本次截止时间变成“常年受理”等不可解析文本：
            # 不能再维持 old，否则默认查询永远看不到这一行。
            new_status = "ok"
    unchanged = (new_title, new_deadline, new_raw, new_project_status, new_status) == (
        old_title,
        old_deadline,
        old_raw,
        old_project_status,
        old_status,
    )
    if old_title_source != "list_title":
        # 旧行可能来自新闻模式（gne_default）；项目行必须带 list_title 标记，
        # 否则查询侧按 title_source 识别项目行会漏掉它。
        unchanged = False
    if (
        new_publish,
        new_publish_utc,
        new_publish_source,
        new_publish_raw,
        new_list_publish,
    ) != (
        old_publish,
        old_publish_utc,
        old_publish_source,
        old_publish_raw,
        old_list_publish,
    ):
        # 值相同但来源/原文不同的历史新闻行（publish_time_source=xpath）也要刷新，
        # 否则项目行永远带着新闻模式的来源标记，无法自愈成 list。
        unchanged = False
    if new_timezone != old_timezone:
        # 接受本次列表日期时补写行内 timezone，让墙钟与 UTC 的换算依据可自证；
        # 沿用旧墙钟且行内时区缺失时 new_timezone 保持空，不把猜测写库。
        unchanged = False
    if unchanged:
        if new_status == "ok" and content_missing:
            return "retry"
        return "skip"
    if keep_legacy_publish:
        # 只在真的要写库时告警：未变化的行每轮都会走到这里，逐轮重复打印
        # 会变成日志噪音。旧值来自新闻模式的详情日期（source=xpath 等），
        # 语义上不是列表发布日期；要清空需临时移除 date_xpath 重抓。
        LOG.warning(
            "站点 %r 的项目行 %s 本次列表日期解析失败，沿用旧发布时间 %r（来源 %r）",
            source_name,
            url,
            new_publish,
            new_publish_source or "空",
        )
    if tz_guessed:
        # 行内时区缺失属于历史数据（v6 之前入库），只能按站点当前配置猜一次
        # UTC；不写回 timezone，站点配置日后被修正时还能靠 --force 重算。
        if timezone_name:
            LOG.warning(
                "站点 %r 的项目行 %s 沿用旧发布时间 %r 但缺少行内时区，"
                "按站点当前时区 %r 换算 publish_time_utc",
                source_name,
                url,
                new_publish,
                timezone_name,
            )
        else:
            # 站点配置也为空时没有可换算的口径，utc 保持 NULL；不要把「没算」
            # 说成「按空时区算过」，否则排障时会误以为已经有可信 UTC 值。
            LOG.warning(
                "站点 %r 的项目行 %s 缺少可用时区（站点配置与行内均未提供），"
                "publish_time_utc 留空",
                source_name,
                url,
            )
    if tz_config_missing:
        # 站点配置缺 timezone 属于配置问题，按行内旧时区换算并提示修正。
        if new_timezone:
            LOG.warning(
                "站点 %r 未配置 timezone，项目行 %s 沿用行内时区 %r 换算 publish_time_utc",
                source_name,
                url,
                new_timezone,
            )
        else:
            LOG.warning(
                "站点 %r 未配置 timezone，项目行 %s 也没有行内时区，"
                "publish_time_utc 留空",
                source_name,
                url,
            )
    # publish_time 在这里按 source 一起写：只有配置了 date_xpath 的站点才有值，
    # 其余站点写空串，与「项目行不伪装发布时间」的语义保持一致。
    utc_value = new_publish_utc or None
    conn.execute(
        "UPDATE articles SET title = ?, list_title = ?, deadline = ?, deadline_raw = ?, "
        "project_status = ?, status = ?, publish_time = ?, publish_time_utc = ?, "
        "publish_time_source = ?, publish_time_raw = ?, list_publish_time = ?, "
        "timezone = ?, title_source = 'list_title', updated_at = ? "
        "WHERE source = ? AND url = ?",
        (
            new_title,
            new_title,
            new_deadline,
            new_raw,
            new_project_status,
            new_status,
            new_publish,
            utc_value,
            new_publish_source,
            new_publish_raw,
            new_list_publish,
            # 空串写成 NULL：行内时区缺失保持「口径未知」，不要留一个空串值。
            new_timezone or None,
            now_iso(),
            source_name,
            url,
        ),
    )
    conn.commit()
    if new_status == "ok" and content_required and (old_status == "old" or content_missing):
        return "retry"
    return new_status


def _process_project_entry(
    conn: sqlite3.Connection,
    source: dict,
    entry: dict,
    cutoff: dt.datetime,
    force: bool,
    delay: float = 0.0,
) -> str:
    """处理项目申报列表条目（listing_type=project_application）。

    与新闻条目的差别：
    - 列表本身即完整数据（标题/截止时间/申报状态/链接），deadline 单独入库；
    - 不把截止时间伪装成发布时间：publish_time 只取列表自己的发布日期
      （date_xpath），未配置 date_xpath 时保持为空；
    - content_required=False（项目站点默认）时，详情页登录墙/无正文仍记为
      ok，只保存列表字段，避免 no_content 每次重跑；配 true 则退回新闻语义。
    """
    url = entry["url"]
    source_name = source["name"]
    tz_name = source.get("timezone", "Asia/Shanghai")
    content_required = bool(source.get("content_required", False))

    # 只认 deadline 列。绝不回退到 parsed（列表发布时间）：项目站点若误配
    # date_xpath 而漏配 deadline_xpath，回退会把发布时间写成截止时间，
    # 并可能据此把仍在受理的公告误判为 old。
    deadline_dt = entry.get("deadline")
    deadline_text = format_publish_time(deadline_dt, tz_name) if deadline_dt else ""
    deadline_raw = entry.get("deadline_text") or ""
    project_status = entry.get("project_status") or ""
    # 列表发布日期（date_xpath）是真实可信的发布时间，单独入库；没配
    # date_xpath 的站点（如只有「截止时间」列的列表）保持为空，绝不拿
    # deadline 顶替。这里与 deadline 完全解耦。
    list_date = entry.get("parsed")
    publish_text = format_publish_time(list_date, tz_name) if list_date else ""
    publish_raw = clean_text(entry.get("date_text")) if publish_text else ""
    if deadline_raw and deadline_dt is None:
        LOG.warning(
            "[%s] 无法解析项目截止时间 %r，deadline 留空：%s",
            source_name,
            deadline_raw,
            url,
        )

    # 已入库的项目行也要用本次列表数据刷新截止时间、申报状态和窗口状态；
    # 只跳过详情抓取，不能整条跳过，否则列表延期/截止状态变化永远不落库。
    if not force:
        refreshed = _refresh_project_listing(
            conn,
            source_name,
            url,
            entry.get("title", ""),
            deadline_text,
            deadline_dt,
            deadline_raw,
            project_status,
            cutoff,
            tz_name,
            content_required,
            publish_text,
            publish_raw,
            bool(source.get("date_xpath")),
        )
        if refreshed is not None and refreshed != "retry":
            return refreshed
        if refreshed != "retry" and should_skip_stored(
            conn, source_name, url, cutoff, tz_name
        ):
            return "skip"

    def _save(status: str, **kwargs: Any) -> str:
        # 项目申报的列表标题就是公告标题；详情页常是登录墙，GNE 抽到的
        # <title> 往往是站点名，因此统一以列表标题作为展示标题。
        kwargs.setdefault("title", entry.get("title", ""))
        kwargs.setdefault("title_source", "list_title")
        # 列表发布日期只在配置了 date_xpath 且解析成功时写入；其余情况留空，
        # 并在下面把同一行可能残留的历史发布时间一并清掉。
        kwargs.setdefault("publish_time", publish_text)
        kwargs.setdefault("publish_time_source", "list" if publish_text else "")
        kwargs.setdefault("publish_time_raw", publish_raw)
        kwargs.setdefault("list_publish_time", publish_text)
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            deadline=deadline_text,
            deadline_raw=deadline_raw,
            project_status=project_status,
            timezone=tz_name,
            status=status,
            **kwargs,
        )
        if not publish_text and not source.get("date_xpath"):
            # 站点没配 date_xpath（含同一 URL 从新闻模式切到项目模式）时，历史
            # publish_time 会让 /v1/articles?published_from=... 误命中项目行，
            # 这里清空。配了 date_xpath 的站点不清：本次列表日期解析失败要沿用
            # 旧值（与 _refresh_project_listing 的保留分支一致），否则正文重试
            # 期间会把刚保留的日期抹掉。分两次提交是有意的：save_article 写业务
            # 字段，这里清理历史发布字段；两次之间崩溃时下一次
            # _refresh_project_listing 会自愈。
            # publish_time_utc 写 NULL 而不是空串，与 save_article 一致：
            # 查询侧对 NULL 回退旧的墙钟比较，空串会让这类行漏掉。
            conn.execute(
                "UPDATE articles SET publish_time = '', publish_time_utc = NULL, "
                "publish_time_source = '', publish_time_raw = '', list_publish_time = '' "
                "WHERE source = ? AND url = ?",
                (source_name, url),
            )
            conn.commit()
        return status

    # 截止时间早于窗口起点视为过期条目，与新闻的 old 语义一致（可用 --days 放宽）。
    if deadline_dt and deadline_dt < cutoff and not force:
        return _save("old")

    if delay > 0:
        time.sleep(delay)
    fetched = fetch_article_html(url)
    if not fetched:
        if content_required:
            return _save("error", error="抓取 HTML 失败（直接抓取和浏览器回退均失败）")
        return _save("ok")

    html, already_rendered = fetched
    result = extract_article(
        html,
        url,
        already_rendered=already_rendered,
        title_xpath=source.get("article_title_xpath") or "",
        publish_time_xpath=source.get("article_publish_time_xpath") or "",
        body_xpath=source.get("article_body_xpath") or "",
    )
    if result is None:
        if content_required:
            return _save("no_content", error="无法提取正文")
        return _save("ok")

    raw_content = str(result.get("content") or "").strip()
    # 登录墙/占位页：GNE 会把站点名当标题、把「还没有账号？立即注册」这类
    # 十几字提示当正文。低于 100 字一律不当作详情内容，只保留列表字段。
    if content_is_too_short(clean_text(raw_content)):
        if content_required:
            return _save("no_content", error="无法提取正文")
        return _save("ok")

    return _save(
        "ok",
        content=clean_text(raw_content),
        raw_content=raw_content,
        images=normalize_images_for_update(result.get("images"), url),
    )


def process_entry(
    conn: sqlite3.Connection,
    source: dict,
    entry: dict,
    cutoff: dt.datetime,
    force: bool,
    delay: float = 0.0,
) -> str:
    """处理单篇文章：去重、时间过滤、抓取、抽取并入库，返回处理结果标签。"""
    if source.get("listing_type") == "project_application":
        return _process_project_entry(conn, source, entry, cutoff, force, delay)
    url = entry["url"]
    source_name = source["name"]
    tz_name = source.get("timezone", "Asia/Shanghai")

    if not force and should_skip_stored(conn, source_name, url, cutoff, tz_name):
        return "skip"

    list_date = entry.get("parsed")
    list_publish_text = format_publish_time(list_date, tz_name) if list_date else ""
    try:
        date_skew_days = max(0, min(365, int(source.get("date_skew_days", 7))))
    except (TypeError, ValueError):
        date_skew_days = 7

    # --force 表示“忽略已抓记录、强制重抓”，因此窗口外条目也不应被直接标 old 短路，
    # 否则 force 对窗口外的历史条目并不生效。
    if list_date and list_date < cutoff and not force:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            publish_time=list_publish_text,
            publish_time_source="list",
            publish_time_raw="",
            list_publish_time=list_publish_text,
            timezone=tz_name,
            status="old",
        )
        return "old"

    # 详情页请求同样遵守站点 delay，否则单站点内会连续无间隔请求，
    # 实测 3 篇文章之间的间隔只有 0.13s（配置 0.5s 未生效）。
    if delay > 0:
        time.sleep(delay)
    fetched = fetch_article_html(url)
    if not fetched:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            list_publish_time=list_publish_text,
            status="error",
            error="抓取 HTML 失败（直接抓取和浏览器回退均失败）",
        )
        return "error"
    html, already_rendered = fetched

    result = extract_article(
        html,
        url,
        already_rendered=already_rendered,
        title_xpath=source.get("article_title_xpath") or "",
        publish_time_xpath=source.get("article_publish_time_xpath") or "",
        body_xpath=source.get("article_body_xpath") or "",
    )
    if result is None:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            list_publish_time=list_publish_text,
            status="no_content",
            error="无法提取正文",
        )
        return "no_content"

    # GNE 的 content 本身保留段落换行，clean_text 会把它们压成空格。
    # content 继续存归一化文本（搜索/摘要/既有下游语义不变），
    # raw_content 存原始换行版本，供正文展示与导出使用。
    raw_content = str(result.get("content") or "").strip()
    publish_raw = clean_text(result.get("publish_time"))
    # 详情日期不允许“未来年份自动减一年”：那会把 2026 年的正文时间
    # 改成 2025 年，从而错误命中 old。解析失败时交给列表日期兜底。
    publish_dt = parse_datetime(publish_raw, None, tz_name, allow_year_adjust=False)
    # 无年份月日按列表日期前后年份取最近结果：列表日期在 1 月初、详情写
    # 去年 12 月时，只按列表年份解析会得到未来日期并返回 None。
    around_applied = False
    if list_date is not None and is_yearless_month_day(publish_raw):
        around = parse_yearless_around(publish_raw, list_date, tz_name)
        if around is not None:
            publish_dt = around
            around_applied = True
    raw_publish_source = str(result.get("publish_time_source") or "")
    publish_source = publish_source_for(raw_publish_source)
    title_source = str(result.get("title_source") or "gne_default")
    if title_source not in ("article_title_xpath", "gne_default"):
        title_source = "gne_default"
    # 无年份的“09月30日 09:30”先按列表日期补年份，再比较偏差：
    # 否则 parse_datetime 用当前年生成 2026-09-30，与 2025-09-30 的列表
    # 日期相差一年，偏差校验会直接换成列表日期并丢掉详情里的 09:30。
    if (
        list_date is not None
        and publish_dt is not None
        and publish_source != "list"
        and is_yearless_month_day(publish_raw)
        and not around_applied
    ):
        reconciled = align_yearless_datetime(publish_dt, list_date, tz_name)
        if reconciled is not None:
            publish_dt = reconciled
        else:
            publish_dt = list_date
            publish_source = "list"
    if list_date is not None:
        if publish_dt is None:
            publish_dt = list_date
            publish_source = "list"
        elif publish_source != "list" and abs(
            (
                publish_dt.astimezone(get_timezone(tz_name)).date()
                - list_date.astimezone(get_timezone(tz_name)).date()
            ).days
        ) > date_skew_days:
            LOG.warning(
                "文章 %s 详情日期 %s 与列表日期 %s 相差超过 %d 天，改用列表日期",
                url,
                publish_dt.isoformat(),
                list_date.isoformat(),
                date_skew_days,
            )
            publish_dt = list_date
            publish_source = "list"
    # 没有可信列表日期时，真正缺少年份的月日不能按当前年猜测；
    # 相对时间（昨天/3小时前）已经解析为完整日期，必须保留。
    if (
        publish_dt is not None
        and publish_source != "list"
        and list_date is None
        and is_yearless_month_day(publish_raw)
    ):
        publish_dt = None
        publish_source = ""
    publish_text = format_publish_time(publish_dt, tz_name) if publish_dt else ""
    # 回退到列表日期时，详情原文不再代表当前发布时间，必须清空；
    # 否则下游会看到 source=list 却挂着一段详情页旧时间文本。
    publish_raw_for_save = "" if publish_source == "list" else publish_raw

    if publish_dt and publish_dt < cutoff:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            title=clean_title(result.get("title")),
            publish_time=publish_text,
            publish_time_source=publish_source,
            publish_time_raw=publish_raw_for_save,
            list_publish_time=list_publish_text,
            title_source=title_source,
            timezone=tz_name,
            author=clean_text(result.get("author")),
            status="old",
        )
        return "old"

    if publish_dt is None:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            title=clean_title(result.get("title")),
            publish_time_source="",
            publish_time_raw=publish_raw_for_save,
            list_publish_time=list_publish_text,
            title_source=title_source,
            content=clean_text(raw_content),
            raw_content=raw_content,
            images=normalize_images_for_update(result.get("images"), url),
            status="unknown_date",
            error="无法解析发布日期",
        )
        return "unknown_date"

    save_article(
        conn,
        source_name,
        url,
        list_title=entry.get("title", ""),
        title=clean_title(result.get("title")),
        publish_time=publish_text,
        publish_time_source=publish_source,
        publish_time_raw=publish_raw_for_save,
        list_publish_time=list_publish_text,
        title_source=title_source,
        timezone=tz_name,
        author=clean_text(result.get("author")),
        content=clean_text(raw_content),
        raw_content=raw_content,
        images=normalize_images_for_update(result.get("images"), url),
        status="ok",
    )
    return "ok"


def run_source(
    raw_source: dict,
    common: dict,
    db_path: str,
    cutoff: dt.datetime,
    force: bool,
    limit_per_source: int,
) -> tuple[str, dict[str, int]]:
    source = {**common, **raw_source}
    # 站名缺失时用 <未命名> 兜底，避免 KeyError 在 try 之外抛出、导致整个抓取轮次中断。
    source_name = str(source.get("name") or "<未命名>")
    counts = {
        "ok": 0,
        "old": 0,
        "skip": 0,
        "no_content": 0,
        "unknown_date": 0,
        "error": 0,
        "list_empty": 0,
    }
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=DB_BUSY_TIMEOUT_MS / 1000)
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
        known_urls: set[str] | None = None
        pagination = _pagination_settings(source)
        if not force and pagination["mode"] != "none" and not source.get("list_urls"):
            known_urls = {
                str(row[0])
                for row in conn.execute(
                    "SELECT url FROM articles WHERE source = ?", (source_name,)
                )
            }
        collected = collect_list_entries_detailed(
            source, cutoff=cutoff, known_urls=known_urls
        )
        entries = collected["items"]
        LOG.info(
            "[%s] 列表抓取 %d 页（停止原因：%s），解析 %d 条",
            source_name,
            collected["pages"],
            collected["stop_reason"],
            len(entries),
        )
        if not entries:
            counts["list_empty"] = 1
            if collected["stop_reason"] == "cutoff":
                LOG.info("[%s] 抓取窗口内没有新条目。", source_name)
            else:
                LOG.warning("[%s] 列表页没有解析到任何条目。", source_name)
        else:
            now = dt.datetime.now(get_timezone(source.get("timezone")))
            entries.sort(key=lambda item: item.get("parsed") or now, reverse=True)
            # limit_per_source 只统计“实际处理”的条目；已入库被跳过的条目
            # 不占用名额，否则增量运行会一直卡在同一批 skip 上无法前进。
            processed = 0
            delay = float(source.get("delay", 0))
            for entry in entries:
                if limit_per_source and processed >= limit_per_source:
                    break
                try:
                    status = process_entry(
                        conn, source, entry, cutoff, force, delay
                    )
                except Exception as exc:
                    LOG.exception(
                        "[%s] 文章处理失败 %s: %s",
                        source_name,
                        entry.get("url"),
                        exc,
                    )
                    try:
                        save_article(
                            conn,
                            source_name,
                            entry.get("url", ""),
                            list_title=entry.get("title", ""),
                            status="error",
                            error=str(exc)[:500],
                        )
                    except Exception:
                        LOG.exception("[%s] 保存错误记录失败", source_name)
                    status = "error"
                counts[status] = counts.get(status, 0) + 1
                if status != "skip":
                    processed += 1
            LOG.info(
                "[%s] 完成：新增/更新 %d，跳过 %d，过期 %d，无正文 %d，"
                "未知日期 %d，错误 %d",
                source_name,
                counts["ok"],
                counts["skip"],
                counts["old"],
                counts["no_content"],
                counts["unknown_date"],
                counts["error"],
            )
    except Exception as exc:
        counts["error"] += 1
        LOG.exception("[%s] 站点处理失败：%s", source_name, exc)
    finally:
        if conn is not None:
            conn.close()
        # 站点处理结束就释放该线程的浏览器，避免 worker 池长期占着 Chromium。
        close_thread_browser()
    return source_name, counts


def cmd_init(args: argparse.Namespace) -> None:
    conn = init_db(args.db)
    conn.close()
    print(f"数据库已初始化：{args.db}")


def cmd_run(args: argparse.Namespace) -> None:
    config_path = resolve_config_path(args.config)
    config = load_config(config_path)
    sources = config["sources"]
    if not sources:
        print(
            f"{config_path} 里还没有配置任何站点。"
            "请参考 README 中的配置说明添加站点。"
        )
        return

    db_path = args.db
    conn = init_db(db_path)
    conn.close()

    if args.since:
        cutoff = dt.datetime.strptime(args.since, "%Y-%m-%d").replace(
            tzinfo=get_timezone(config.get("timezone", "Asia/Shanghai"))
        )
    else:
        cutoff = dt.datetime.now(
            get_timezone(config.get("timezone", "Asia/Shanghai"))
        ) - dt.timedelta(days=args.days or config.get("days", 3))

    workers = args.workers or int(config.get("workers", 16))
    workers = max(1, min(workers, len(sources)))
    print(
        f"开始抓取 {len(sources)} 个站点（最近 {args.days or config.get('days', 3)} 天）"
    )

    total: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                run_source,
                raw_source,
                config,
                db_path,
                cutoff,
                args.force,
                args.limit_per_source,
            )
            for raw_source in sources
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                name, counts = future.result()
            except Exception as exc:
                # 单个站点任务抛出未捕获异常时隔离，不影响其余站点继续汇总。
                LOG.exception("站点任务异常：%s", exc)
                continue
            for key, value in counts.items():
                total[key] = total.get(key, 0) + value
            print(f"[{name}] " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    print("\n汇总：" + ", ".join(f"{k}={v}" for k, v in sorted(total.items())))
    print("最新结果可查询 SQLite：")
    print(
        "SELECT source, title, publish_time, url FROM articles "
        "WHERE status='ok' ORDER BY publish_time DESC;"
    )


def cmd_test(args: argparse.Namespace) -> None:
    config = load_config(resolve_config_path(args.config))
    sources = config["sources"]
    if args.name:
        sources = [s for s in sources if s.get("name") == args.name]
    if not sources:
        print("没有匹配的站点。已配置的站点：")
        for source in config["sources"]:
            print(" -", source.get("name"))
        return

    try:
        for raw_source in sources:
            source = {**config, **raw_source}
            print(f"\n===== {source['name']} =====")
            try:
                collected = collect_list_entries_detailed(source)
                entries = collected["items"]
                for entry in entries[: args.limit]:
                    parsed = entry.get("deadline") or entry.get("parsed")
                    date_show = (
                        parsed.strftime("%Y-%m-%d %H:%M")
                        if parsed
                        else (
                            entry.get("deadline_text")
                            or entry.get("date_text")
                            or "日期未知"
                        )
                    )
                    status_show = entry.get("project_status") or ""
                    title = entry["title"]
                    url = entry["url"]
                    suffix = f" [{status_show}]" if status_show else ""
                    print(f"{date_show:<20} {title[:50]:<52}{suffix} {url}")
                print(
                    f"共解析到 {len(entries)} 条（{collected['pages']} 页，"
                    f"停止原因：{collected['stop_reason']}）"
                )
            except Exception as exc:
                LOG.exception("测试站点失败：%s", exc)
    finally:
        # 列表页可能需要浏览器渲染，这里同样要回收主线程的 Chromium。
        close_thread_browser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 GNE 的多站点新闻抓取器")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="初始化 SQLite 数据库")
    init_parser.add_argument("--db", default=DEFAULT_DB_FILE)
    init_parser.set_defaults(func=cmd_init)

    run_parser = subparsers.add_parser("run", help="运行抓取任务")
    run_parser.add_argument("--config", default=DEFAULT_SOURCES_FILE)
    run_parser.add_argument("--db", default=DEFAULT_DB_FILE)
    run_parser.add_argument("--days", type=int, default=0, help="抓取最近 N 天")
    run_parser.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    run_parser.add_argument("--workers", type=int, default=0, help="并发站点数")
    run_parser.add_argument(
        "--limit-per-source",
        type=int,
        default=0,
        help="每个站点最多处理多少条（0 为不限制）",
    )
    run_parser.add_argument("--force", action="store_true", help="强制重新抓取")
    run_parser.set_defaults(func=cmd_run)

    test_parser = subparsers.add_parser("test", help="只测试列表页解析，不抓正文")
    test_parser.add_argument("--config", default=DEFAULT_SOURCES_FILE)
    test_parser.add_argument("--name", default="", help="只测试指定站点")
    test_parser.add_argument("--limit", type=int, default=10)
    test_parser.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
