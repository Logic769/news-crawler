"""SQLite read model for the public API."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from news_crawler import article_id_for, load_config, resolve_config_path
from news_api.timeutil import (
    iso_or_none,
    local_iso,
    resolve_timezone,
    utc_iso_or_none,
)


def connect(db_path: str, *, read_only: bool = True) -> sqlite3.Connection:
    if read_only:
        # mode=ro 让缺失的数据库直接报错，而不是静默创建空文件；
        # 路径需转义 ?/#，否则会被当成 URI 参数。注意 WAL 库仍需要目录可写
        # 才能创建 -shm/-wal，只读挂载的卷请改用不可变快照或只读模式关闭。
        # Windows 盘符路径的反斜杠会被当成转义序列，统一转成 POSIX 形式再编码。
        posix_path = Path(db_path).as_posix()
        target = f"file:{quote(posix_path, safe='/:')}?mode=ro"
        conn = sqlite3.connect(target, timeout=30, uri=True)
    else:
        conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
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
    except Exception:
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
    content_length = (
        int(row["content_length"] or 0) if "content_length" in keys else len(content)
    )
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
        "timezone": tz_name,
        "content": content if include_content else None,
        "content_length": content_length,
        "images": _images(row["images"]),
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
    q: str | None,
    page: int,
    page_size: int,
    order: str,
    include_content: bool,
    timezones: dict[str, str],
    default_tz: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    if source:
        where.append("source = ?")
        params.append(source)
    if published_from:
        where.append("publish_time >= ?")
        params.append(published_from)
    if published_to:
        where.append("publish_time <= ?")
        params.append(published_to)
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
    }.get(order, "publish_time DESC, source ASC, url ASC")

    with read_connection(db_path) as conn:
        conn.execute("BEGIN")
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM articles{where_sql}", params
            ).fetchone()["n"]
        )
        offset = (page - 1) * page_size
        columns = "*" if include_content else (
            "article_id, source, url, list_title, title, publish_time, timezone, "
            "author, length(content) AS content_length, images, status, error, "
            "first_seen_at, updated_at"
        )
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
            "SELECT * FROM articles WHERE article_id = ?", (article_id,)
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
                   MAX(publish_time) AS latest_publish,
                   -- 取最新那条记录写入的时区，保证与 /v1/articles 的口径一致
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
        latest = conn.execute(
            "SELECT MAX(publish_time) AS latest FROM articles"
        ).fetchone()["latest"]
        sources = int(
            conn.execute("SELECT COUNT(DISTINCT source) AS n FROM articles")
            .fetchone()["n"]
        )
        conn.commit()
    return {
        "total": total,
        "by_status": {row["status"]: int(row["n"]) for row in status_rows},
        "sources": sources,
        "latest_published_at": iso_or_none(latest, default_tz),
        "generated_at": dt.datetime.now(resolve_timezone(default_tz))
        .replace(microsecond=0)
        .isoformat(),
    }


def count_articles(db_path: str) -> int:
    with read_connection(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"])
