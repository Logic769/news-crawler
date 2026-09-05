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
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import lxml.html
from gne import GeneralNewsExtractor, NoContentException

from url_extract import content_needs_render, fetch_html, fetch_html_with_browser

LOG = logging.getLogger("news_crawler")

DEFAULT_SOURCES_FILE = "sources.json"
DEFAULT_DB_FILE = "news.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    list_title   TEXT,
    title        TEXT,
    publish_time TEXT,
    author       TEXT,
    content      TEXT,
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
"""


def now_iso() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def get_timezone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_datetime(
    value: Any,
    date_format: str | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dt.datetime | None:
    """Parse common Chinese news date strings into an aware datetime."""
    text = clean_text(value)
    if not text:
        return None

    tz = get_timezone(timezone_name)
    now = dt.datetime.now(tz)

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
            return parsed.replace(tzinfo=tz)
        except ValueError:
            pass

    # Full year: 2026-08-26 / 2026年8月26日 / 2026-08-26 10:30:00
    full = re.search(
        r"(20\d{2}|\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"
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
        if parsed > now + dt.timedelta(days=1):
            parsed = parsed.replace(year=parsed.year - 1)
        return parsed

    # Month/day only: 08-26 / 8月26日 / 08-26 10:30
    short = re.search(
        r"(?<!\d)(\d{1,2})[-/月.](\d{1,2})(?:日)?"
        r"(?:[ T]+(\d{1,2})[:：](\d{1,2})(?::(\d{1,2}))?)?",
        text,
    )
    if short:
        year = now.year
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
        if parsed > now + dt.timedelta(days=1):
            parsed = parsed.replace(year=parsed.year - 1)
        return parsed

    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def load_config(path: str) -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(
            f"找不到配置文件 {path}。可先复制 sources.example.json 为 sources.json。"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = {"sources": data}
    data.setdefault("sources", [])
    data.setdefault("workers", 3)
    data.setdefault("days", 3)
    data.setdefault("timezone", "Asia/Shanghai")
    data.setdefault("delay", 0.5)
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
    parts = []
    for value in values:
        if isinstance(value, str):
            parts.append(value)
        elif hasattr(value, "text_content"):
            parts.append(value.text_content() or "")
    return clean_text(" ".join(parts))


def xpath_href(node, xpath: str | None) -> str:
    try:
        values = node.xpath(normalize_xpath(xpath or ".//a/@href"))
    except Exception:
        values = []
    if isinstance(values, str):
        return clean_text(values)
    for value in values:
        href = clean_text(value)
        if href and not href.startswith("javascript:"):
            return href
    return ""


def parse_list_items(html: str, source: dict, base_url: str) -> list[dict]:
    doc = lxml.html.fromstring(html)
    nodes = doc.xpath(source["item_xpath"])
    tz_name = source.get("timezone", "Asia/Shanghai")
    date_format = source.get("date_format")
    items: list[dict] = []
    seen: set[str] = set()

    for node in nodes:
        title = xpath_text(node, source.get("title_xpath") or ".//a")
        href = xpath_href(node, source.get("url_xpath"))
        if not title and not href:
            continue
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        date_text = xpath_text(node, source.get("date_xpath") or ".")
        parsed = parse_datetime(date_text, date_format, tz_name)
        if parsed is None:
            # Some sites put the publish date in the article URL instead,
            # for example https://.../2026/09/05/article.shtml
            parsed = parse_datetime(url, None, tz_name)
        items.append(
            {
                "title": title,
                "url": url,
                "date_text": date_text,
                "parsed": parsed,
            }
        )
    return items


def fetch_list_html(url: str, render: bool) -> str | None:
    if render:
        return fetch_html_with_browser(url)
    try:
        return fetch_html(url)
    except Exception as exc:
        LOG.warning("直接抓取列表失败 %s (%s)，尝试浏览器。", url, exc)
        return fetch_html_with_browser(url)


def collect_list_entries(source: dict) -> list[dict]:
    urls = source.get("list_urls") or [source["list_url"]]
    all_items: list[dict] = []
    for list_url in urls:
        render = bool(source.get("render"))
        html = fetch_list_html(list_url, render)
        if html:
            items = parse_list_items(html, source, list_url)
            if not items and not render:
                LOG.info("列表解析为空，尝试 Chrome 渲染 %s", list_url)
                rendered = fetch_html_with_browser(list_url)
                if rendered:
                    items = parse_list_items(rendered, source, list_url)
            all_items.extend(items)
        time.sleep(float(source.get("delay", 0)))
    return all_items


def fetch_article_html(url: str) -> str | None:
    try:
        return fetch_html(url)
    except Exception as exc:
        LOG.warning("直接抓取文章失败 %s (%s)，尝试浏览器。", url, exc)
        return fetch_html_with_browser(url)


def extract_article(html: str, url: str) -> dict | None:
    extractor = GeneralNewsExtractor()
    try:
        result = extractor.extract(html)
    except NoContentException:
        result = None
    if result is None or content_needs_render(result.get("content")):
        LOG.info("正文为空，尝试 Chrome 渲染 %s", url)
        rendered = fetch_html_with_browser(url)
        if rendered:
            try:
                result = extractor.extract(rendered)
            except NoContentException:
                result = None
    if result is None or content_needs_render(result.get("content")):
        return None
    return result


def save_article(
    conn: sqlite3.Connection,
    source: str,
    url: str,
    *,
    list_title: str = "",
    title: str = "",
    publish_time: str = "",
    author: str = "",
    content: str = "",
    images: list[str] | None = None,
    status: str,
    error: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO articles (
            source, url, list_title, title, publish_time, author,
            content, images, status, error, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, url) DO UPDATE SET
            list_title = excluded.list_title,
            title = excluded.title,
            publish_time = excluded.publish_time,
            author = excluded.author,
            content = excluded.content,
            images = excluded.images,
            status = excluded.status,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            source,
            url,
            list_title,
            title,
            publish_time,
            author,
            content,
            json.dumps(images or [], ensure_ascii=False),
            status,
            error,
            now_iso(),
            now_iso(),
        ),
    )
    conn.commit()


def process_entry(
    conn: sqlite3.Connection,
    source: dict,
    entry: dict,
    cutoff: dt.datetime,
    force: bool,
) -> str:
    url = entry["url"]
    source_name = source["name"]

    if not force:
        row = conn.execute(
            "SELECT status FROM articles WHERE source = ? AND url = ?",
            (source_name, url),
        ).fetchone()
        if row and row[0] in ("ok", "old"):
            return "skip"

    list_date = entry.get("parsed")
    if list_date and list_date < cutoff:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            publish_time=list_date.strftime("%Y-%m-%d %H:%M:%S"),
            status="old",
        )
        return "old"

    html = fetch_article_html(url)
    if not html:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            status="error",
            error="抓取 HTML 失败（直接抓取和浏览器回退均失败）",
        )
        return "error"

    result = extract_article(html, url)
    if result is None:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            status="no_content",
            error="无法提取正文",
        )
        return "no_content"

    publish_text = clean_text(result.get("publish_time"))
    publish_dt = parse_datetime(
        publish_text,
        None,
        source.get("timezone", "Asia/Shanghai"),
    )
    if publish_dt is None:
        publish_dt = list_date
        if list_date:
            publish_text = list_date.strftime("%Y-%m-%d %H:%M:%S")

    if publish_dt and publish_dt < cutoff:
        save_article(
            conn,
            source_name,
            url,
            list_title=entry.get("title", ""),
            title=clean_text(result.get("title")),
            publish_time=publish_text,
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
            title=clean_text(result.get("title")),
            content=clean_text(result.get("content")),
            images=result.get("images") or [],
            status="unknown_date",
            error="无法解析发布日期",
        )
        return "unknown_date"

    save_article(
        conn,
        source_name,
        url,
        list_title=entry.get("title", ""),
        title=clean_text(result.get("title")),
        publish_time=publish_text,
        author=clean_text(result.get("author")),
        content=clean_text(result.get("content")),
        images=result.get("images") or [],
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
    source_name = source["name"]
    counts = {
        "ok": 0,
        "old": 0,
        "skip": 0,
        "no_content": 0,
        "unknown_date": 0,
        "error": 0,
        "list_empty": 0,
    }
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        entries = collect_list_entries(source)
        if not entries:
            counts["list_empty"] = 1
            LOG.warning("[%s] 列表页没有解析到任何条目。", source_name)
        else:
            now = dt.datetime.now(get_timezone(source.get("timezone")))
            entries.sort(key=lambda item: item.get("parsed") or now, reverse=True)
            processed = 0
            for entry in entries:
                if limit_per_source and processed >= limit_per_source:
                    break
                status = process_entry(conn, source, entry, cutoff, force)
                counts[status] = counts.get(status, 0) + 1
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
        conn.close()
    return source_name, counts


def cmd_init(args: argparse.Namespace) -> None:
    conn = init_db(args.db)
    conn.close()
    print(f"数据库已初始化：{args.db}")


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    sources = config["sources"]
    if not sources:
        print(
            "sources.json 里还没有配置任何站点。"
            "请参考 sources.example.json 填写后重试。"
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

    workers = args.workers or int(config.get("workers", 3))
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
            name, counts = future.result()
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
    config = load_config(args.config)
    sources = config["sources"]
    if args.name:
        sources = [s for s in sources if s.get("name") == args.name]
    if not sources:
        print("没有匹配的站点。已配置的站点：")
        for source in config["sources"]:
            print(" -", source.get("name"))
        return

    for raw_source in sources:
        source = {**config, **raw_source}
        print(f"\n===== {source['name']} =====")
        try:
            entries = collect_list_entries(source)
            for entry in entries[: args.limit]:
                parsed = entry.get("parsed")
                date_show = (
                    parsed.strftime("%Y-%m-%d %H:%M")
                    if parsed
                    else (entry.get("date_text") or "日期未知")
                )
                title = entry["title"]
                url = entry["url"]
                print(f"{date_show:<20} {title[:50]:<52} {url}")
            print(f"共解析到 {len(entries)} 条")
        except Exception as exc:
            LOG.exception("测试站点失败：%s", exc)


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
