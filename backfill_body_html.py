"""回填 articles.body_html：重抓历史文章，补齐清洗后的正文 HTML。

前端详情页用 v-html 渲染 body_html；v9 之前入库的历史行没有该字段，
只能回到原页面重新抽取。脚本只写 body_html，不动 content / raw_content /
status / updated_at，失败行原样保留，可重复运行（已回填的行不再是候选）。

宿主机：
    python -X utf8 backfill_body_html.py --db data/news.db --dry-run
    python -X utf8 backfill_body_html.py --db data/news.db --limit 50

容器内：
    docker compose run --rm --entrypoint python crawler -X utf8 backfill_body_html.py --db /app/data/news.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from backfill_raw_content import load_source_xpaths
from gne import GeneralNewsExtractor
from news_crawler import DEFAULT_DB_FILE, DEFAULT_SOURCES_FILE, init_db
from url_extract import close_thread_browser, extract_article

BACKFILL_STATUSES = ("ok", "unknown_date")

_UPDATE_SQL = (
    "UPDATE articles SET body_html = ? "
    "WHERE source = ? AND url = ? AND (body_html IS NULL OR body_html = '')"
)


def find_candidates(
    conn: sqlite3.Connection,
    source: str,
    limit: int,
    skip: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    placeholders = ", ".join("?" * len(BACKFILL_STATUSES))
    sql = (
        "SELECT source, url FROM articles "
        "WHERE (body_html IS NULL OR body_html = '') "
        f"AND status IN ({placeholders})"
    )
    params: list[object] = list(BACKFILL_STATUSES)
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += (
        " ORDER BY CASE WHEN publish_time IS NULL OR publish_time = '' "
        "THEN 0 ELSE 1 END, publish_time DESC, source, url"
    )
    if limit > 0 and not skip:
        sql += " LIMIT ?"
        params.append(limit)
    rows: list[tuple[str, str]] = []
    cursor = conn.execute(sql, params)
    try:
        for row in cursor:
            candidate = (str(row[0]), str(row[1]))
            if skip and candidate in skip:
                continue
            rows.append(candidate)
            if limit > 0 and len(rows) >= limit:
                break
    finally:
        cursor.close()
    return rows


def backfill(
    conn: sqlite3.Connection,
    candidates: list[tuple[str, str]],
    *,
    delay: float,
    allow_browser: bool,
    dry_run: bool,
    source_xpaths: dict[str, str] | None = None,
) -> tuple[int, int, int, int]:
    """逐条重抓并写回 body_html。

    返回 (成功数, 失败数, 跳过数, 写库失败数)。跳过指 UPDATE 未命中，
    通常是另一个进程已回填或行已被删除。
    """
    if dry_run or not candidates:
        return 0, 0, 0, 0

    source_xpaths = source_xpaths or {}
    extractor = GeneralNewsExtractor()
    updated = failed = skipped = write_failed = 0
    try:
        for index, (source, url) in enumerate(candidates):
            if index and delay > 0:
                time.sleep(delay)
            try:
                result = extract_article(
                    url,
                    extractor,
                    allow_browser=allow_browser,
                    body_xpath=source_xpaths.get(source, ""),
                )
                body_html = str((result or {}).get("body_html") or "").strip()
            except Exception as exc:  # 单条失败不能中止整批
                failed += 1
                print(f"  [失败] {source} {url}：{exc}")
                continue
            if not body_html:
                failed += 1
                print(f"  [失败] {source} {url}：抽取结果没有正文 HTML")
                continue
            try:
                cursor = conn.execute(_UPDATE_SQL, (body_html, source, url))
                conn.commit()
            except sqlite3.Error as exc:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                failed += 1
                write_failed += 1
                print(f"  [失败] {source} {url}：写库失败 {exc}")
                continue
            if cursor.rowcount:
                updated += 1
                print(f"  [回填 {len(body_html)} 字符] {source} {url}")
            else:
                skipped += 1
                print(f"  [跳过] {source} {url}（已被其他进程回填或条件不再满足）")
    finally:
        close_thread_browser()
    return updated, failed, skipped, write_failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填 articles.body_html：重抓历史行，补齐清洗后的正文 HTML")
    parser.add_argument("--db", default=DEFAULT_DB_FILE)
    parser.add_argument("--config", default=DEFAULT_SOURCES_FILE)
    parser.add_argument("--source", default="", help="只回填指定站点")
    parser.add_argument("--limit", type=int, default=0, help="本次最多尝试多少条，0 为不限制")
    parser.add_argument("--delay", type=float, default=0.3, help="两次请求之间的等待秒数，默认 0.3")
    parser.add_argument("--allow-browser", action="store_true", help="直接抓取失败时允许回退本机 Chrome")
    parser.add_argument("--dry-run", action="store_true", help="只统计候选数量，不抓取")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        print("--limit 不能为负数（0 表示不限制）", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"找不到数据库：{db_path}", file=sys.stderr)
        return 2
    conn = init_db(str(db_path))
    try:
        candidates = find_candidates(conn, args.source, args.limit)
        print(f"候选 {len(candidates)} 条（status in {'/'.join(BACKFILL_STATUSES)} 且 body_html 为空）")
        source_xpaths = {} if args.dry_run else load_source_xpaths(args.config)
        updated, failed, skipped, write_failed = backfill(
            conn,
            candidates,
            delay=args.delay,
            allow_browser=args.allow_browser,
            dry_run=args.dry_run,
            source_xpaths=source_xpaths,
        )
    finally:
        conn.close()
    if args.dry_run:
        print("dry-run：未抓取，未写入任何正文 HTML。")
        return 0
    print(f"完成：回填 {updated} 条，失败 {failed} 条，跳过 {skipped} 条。")
    return 1 if write_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
