"""SQLite read model for the public API."""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from news_api.timeutil import (
    iso_or_none,
    local_iso,
    resolve_timezone,
    utc_iso_or_none,
)
from news_crawler import (
    DB_BUSY_TIMEOUT_MS,
    article_id_for,
    load_config,
    resolve_config_path,
)
from url_extract import normalize_images

LOG = logging.getLogger("news_api.db")


def connect(db_path: str, *, read_only: bool = True) -> sqlite3.Connection:
    if read_only:
        # mode=ro 让缺失的数据库直接报错，而不是静默创建空文件；
        # 路径需转义 ?/#，否则会被当成 URI 参数。注意 WAL 库仍需要目录可写
        # 才能创建 -shm/-wal，只读挂载的卷请改用不可变快照或只读模式关闭。
        # Windows 盘符路径的反斜杠会被当成转义序列，统一转成 POSIX 形式再编码。
        posix_path = Path(db_path).as_posix()
        target = f"file:{quote(posix_path, safe='/:')}?mode=ro"
        conn = sqlite3.connect(
            target, timeout=DB_BUSY_TIMEOUT_MS / 1000, uri=True
        )
    else:
        conn = sqlite3.connect(db_path, timeout=DB_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def read_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def load_timezones(config_path: str, default_tz: str) -> dict[str, str]:
    """Map source name -> configured timezone; tolerate a missing config."""
    try:
        config = load_config(resolve_config_path(config_path))
    except Exception as exc:
        # 配置缺失/损坏时不能静默：下游会把所有来源按 default_tz 处理，
        # 混时区场景会因此错位，必须留下 warning 便于定位。
        LOG.warning(
            "加载时区配置失败，按默认时区 %s 处理：%s（%s）",
            default_tz,
            config_path,
            exc,
        )
        return {}
    fallback = config.get("timezone") or default_tz
    result: dict[str, str] = {}
    for source in config.get("sources", []):
        name = str(source.get("name") or "").strip()
        if name:
            result[name] = str(source.get("timezone") or fallback)
    return result


def _source_timezone(
    source: str, timezones: dict[str, str], default_tz: str
) -> str:
    return timezones.get(source, default_tz)


def _wall_bound_to_utc(value: str, tz_name: str) -> str:
    """把查询边界（默认时区的墙钟串）换算成 UTC 墙钟串，用于同口径过滤。"""
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value
    parsed = parsed.replace(tzinfo=resolve_timezone(tz_name))
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _like_needle(value: str) -> str:
    """Escape LIKE wildcards so the user query is matched literally."""
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _images(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


def row_to_article(
    row: sqlite3.Row,
    *,
    timezones: dict[str, str],
    default_tz: str,
    include_content: bool,
) -> dict[str, Any]:
    source = str(row["source"])
    url = str(row["url"])
    keys = row.keys()
    # 写入时落库的时区优先；旧行没有记录时才回落到当前配置。
    stored_tz = str(row["timezone"] or "").strip() if "timezone" in keys else ""
    tz_name = stored_tz or _source_timezone(source, timezones, default_tz)
    content = (row["content"] or "") if "content" in keys else ""
    stored_length = row["content_length"] if "content_length" in keys else None
    content_length = int(stored_length) if stored_length is not None else len(content)
    article_id = row["article_id"] or article_id_for(source, url)
    return {
        "id": article_id,
        "source": source,
        "url": url,
        "title": row["title"] or "",
        "list_title": row["list_title"] or "",
        "author": row["author"] or "",
        "published_at": iso_or_none(row["publish_time"], tz_name),
        "published_at_utc": utc_iso_or_none(row["publish_time"], tz_name),
        "published_raw": row["publish_time"] or "",
        "publish_time_source": (row["publish_time_source"] or "")
        if "publish_time_source" in keys
        else "",
        "publish_time_raw": (row["publish_time_raw"] or "")
        if "publish_time_raw" in keys
        else "",
        "list_publish_time": (row["list_publish_time"] or "")
        if "list_publish_time" in keys
        else "",
        "title_source": (row["title_source"] or "") if "title_source" in keys else "",
        # 项目申报特化字段：新闻站点为空字符串。
        "deadline": (row["deadline"] or "") if "deadline" in keys else "",
        "deadline_raw": (row["deadline_raw"] or "") if "deadline_raw" in keys else "",
        "project_status": (row["project_status"] or "")
        if "project_status" in keys
        else "",
        "timezone": tz_name,
        "content": content if include_content else None,
        # 保留段落换行的原始正文；与 content 一样只在详情（include_content）中返回。
        "raw_content": (row["raw_content"] or "")
        if include_content and "raw_content" in keys
        else None,
        "content_length": content_length,
        # 历史行可能存着 //cdn/x.jpg 这类协议相对地址（v1.1.1 之前写入的），
        # 读取时按文章 URL 归一化，保证 /v1 对外始终是可直接使用的绝对地址。
        "images": normalize_images(_images(row["images"]), url),
        "status": row["status"],
        "error": row["error"] or None,
        "fetched_at": local_iso(row["first_seen_at"]),
        "updated_at": local_iso(row["updated_at"]),
    }


def query_articles(
    db_path: str,
    *,
    status: str | None,
    source: str | None,
    published_from: str | None,
    published_to: str | None,
    deadline_from: str | None,
    deadline_to: str | None,
    q: str | None,
    page: int,
    page_size: int,
    order: str,
    include_content: bool,
    timezones: dict[str, str],
    default_tz: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    if source:
        where.append("source = ?")
        params.append(source)
    # 时间过滤按真实时刻（publish_time_utc）比较，避免 UTC 站点与 UTC+8 站点
    # 的墙钟串直接相减造成边界错位；历史行没有 publish_time_utc 时回退旧的
    # 墙钟比较，保证升级前后这些行的命中集合不变。
    if published_from:
        where.append(
            "(publish_time_utc >= ? "
            "OR (COALESCE(publish_time_utc, '') = '' AND publish_time >= ?))"
        )
        params.extend(
            [_wall_bound_to_utc(published_from, default_tz), published_from]
        )
    if published_to:
        where.append(
            "(publish_time_utc <= ? "
            "OR (COALESCE(publish_time_utc, '') = '' AND publish_time <= ?))"
        )
        params.extend(
            [_wall_bound_to_utc(published_to, default_tz), published_to]
        )
    # 项目申报公告没有 publish_time，按截止时间过滤/排序用 deadline 列。
    if deadline_from:
        where.append("deadline <> '' AND deadline >= ?")
        params.append(deadline_from)
    if deadline_to:
        where.append("deadline <> '' AND deadline <= ?")
        params.append(deadline_to)
    if q:
        where.append(
            "(title LIKE ? ESCAPE '!' OR content LIKE ? ESCAPE '!' "
            "OR list_title LIKE ? ESCAPE '!')"
        )
        needle = _like_needle(q)
        params.extend([needle, needle, needle])

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "published_desc": "publish_time DESC, source ASC, url ASC",
        "published_asc": "publish_time ASC, source ASC, url ASC",
        "updated_desc": "updated_at DESC, source ASC, url ASC",
        "deadline_desc": (
            "NULLIF(deadline, '') IS NULL, deadline DESC, source ASC, url ASC"
        ),
        "deadline_asc": (
            "NULLIF(deadline, '') IS NULL, deadline ASC, source ASC, url ASC"
        ),
    }.get(order, "publish_time DESC, source ASC, url ASC")

    with read_connection(db_path) as conn:
        conn.execute("BEGIN")
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM articles{where_sql}", params
            ).fetchone()["n"]
        )
        offset = (page - 1) * page_size
        # 列表接口用显式列清单：include_content=true 时给 content，但始终不给
        # raw_content（这列体积约为正文两倍，只经由详情接口返回）。
        if include_content:
            columns = (
                "article_id, source, url, list_title, title, publish_time, timezone, "
                "publish_time_source, publish_time_raw, list_publish_time, title_source, "
                "deadline, deadline_raw, project_status, "
                "author, content, COALESCE(content_length, length(content)) AS content_length, "
                "images, status, error, first_seen_at, updated_at"
            )
        else:
            columns = (
                "article_id, source, url, list_title, title, publish_time, timezone, "
                "publish_time_source, publish_time_raw, list_publish_time, title_source, "
                "deadline, deadline_raw, project_status, "
                "author, COALESCE(content_length, length(content)) AS content_length, "
                "images, status, error, first_seen_at, updated_at"
            )
        # OFFSET 无界会被 ?page=999999999 放大成整表单扫；total 已知时越界直接返回空页。
        if offset >= total:
            rows = []
        else:
            rows = conn.execute(
                f"SELECT {columns} FROM articles{where_sql} "
                f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        conn.commit()

    data = [
        row_to_article(
            row,
            timezones=timezones,
            default_tz=default_tz,
            include_content=include_content,
        )
        for row in rows
    ]
    total_pages = (total + page_size - 1) // page_size if total else 0
    meta = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page * page_size < total,
    }
    return data, meta


def get_article(
    db_path: str,
    article_id: str,
    *,
    timezones: dict[str, str],
    default_tz: str,
) -> dict[str, Any] | None:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "SELECT article_id, source, url, list_title, title, publish_time, "
            "timezone, publish_time_source, publish_time_raw, list_publish_time, "
            "title_source, deadline, deadline_raw, project_status, "
            "author, content, content_length, raw_content, images, "
            "status, error, first_seen_at, updated_at "
            "FROM articles WHERE article_id = ?",
            (article_id,),
        ).fetchone()
    if row is None:
        return None
    return row_to_article(
        row, timezones=timezones, default_tz=default_tz, include_content=True
    )


def list_sources(
    db_path: str, *, timezones: dict[str, str], default_tz: str
) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
                   -- 与 latest_tz 用同一排序取同一条记录，避免 MAX(publish_time)
                   -- 与子查询挑中不同行（并列/时区不同）导致时间被按错误时区解释。
                   (SELECT a2.publish_time FROM articles a2
                     WHERE a2.source = a.source
                     ORDER BY a2.publish_time DESC, a2.updated_at DESC
                     LIMIT 1) AS latest_publish,
                   (SELECT a2.timezone FROM articles a2
                     WHERE a2.source = a.source
                     ORDER BY a2.publish_time DESC, a2.updated_at DESC
                     LIMIT 1) AS latest_tz,
                   MAX(updated_at) AS latest_update
            FROM articles a
            GROUP BY source
            ORDER BY source ASC
            """
        ).fetchall()
    return [
        {
            "source": row["source"],
            "total": int(row["total"]),
            "ok": int(row["ok"] or 0),
            "latest_published_at": iso_or_none(
                row["latest_publish"],
                str(row["latest_tz"] or "").strip()
                or _source_timezone(row["source"], timezones, default_tz),
            ),
            "latest_updated_at": local_iso(row["latest_update"]),
        }
        for row in rows
    ]


def stats(db_path: str, *, default_tz: str) -> dict[str, Any]:
    with read_connection(db_path) as conn:
        # 四条查询必须看到同一快照，否则 total 与 by_status 可能互相矛盾。
        conn.execute("BEGIN")
        total = int(
            conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        )
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM articles GROUP BY status ORDER BY status"
        ).fetchall()
        # 与 /v1/sources 口径一致：取“最新那条记录”的 publish_time，并用该行
        # 落库的时区来解释，而不是全局套用 default_tz（否则跨时区站点会偏移）。
        latest_row = conn.execute(
            "SELECT publish_time, timezone FROM articles "
            "ORDER BY publish_time DESC, updated_at DESC LIMIT 1"
        ).fetchone()
        sources = int(
            conn.execute("SELECT COUNT(DISTINCT source) AS n FROM articles")
            .fetchone()["n"]
        )
        conn.commit()
    latest_publish = latest_row["publish_time"] if latest_row else None
    latest_tz = str((latest_row["timezone"] if latest_row else "") or "").strip() or default_tz
    return {
        "total": total,
        "by_status": {row["status"]: int(row["n"]) for row in status_rows},
        "sources": sources,
        "latest_published_at": iso_or_none(latest_publish, latest_tz),
        "generated_at": dt.datetime.now(resolve_timezone(default_tz))
        .replace(microsecond=0)
        .isoformat(),
    }


def ping_database(db_path: str) -> None:
    """Cheap liveness probe for /v1/health (no full-table COUNT).

    Raises if the database cannot be opened or read.
    """
    with read_connection(db_path) as conn:
        conn.execute("SELECT 1 FROM articles LIMIT 1").fetchone()


def count_articles(db_path: str) -> int:
    with read_connection(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"])
