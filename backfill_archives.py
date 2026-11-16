"""回填公告原文归档。

历史文章（本功能上线前抓取）没有原始 HTML，默认先用库内 body_html / raw_content
生成正文快照（kind=body_html），保证「原文可追溯」先有最小闭环；需要真正原始
页面时再显式加 --fetch-html 逐条重抓（kind=raw_html）。

用法：
    python -X utf8 backfill_archives.py --db data/news.db --dry-run
    python -X utf8 backfill_archives.py --db data/news.db --limit 50
    python -X utf8 backfill_archives.py --db data/news.db --fetch-html --limit 20

约束：归档失败不影响已有文章数据；--fetch-html 受 --delay 限速，默认 0.5s；
已存在同类型快照的行会跳过，可重复运行。
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import article_archive
import news_crawler
from url_extract import fetch_html


def _candidates(
    conn: sqlite3.Connection, *, kind: str, fetch_html: bool, limit: int
) -> list[sqlite3.Row]:
    # 默认回填正文快照；--fetch-html 只挑还没有 raw_html 的行。两者都要求
    # 文章曾经成功抓取过（ok/unknown_date），不把登录墙/失败页当原文。
    existing_kind = article_archive.KIND_RAW_HTML if fetch_html else kind
    sql = (
        "SELECT a.article_id, a.source, a.url, a.body_html, a.raw_content, a.content "
        "FROM articles a WHERE a.status IN ('ok', 'unknown_date') "
        "AND a.article_id IS NOT NULL AND a.article_id <> '' "
        "AND NOT EXISTS (SELECT 1 FROM article_archives ar "
        "WHERE ar.article_id = a.article_id AND ar.kind = ?) "
        "ORDER BY a.publish_time DESC, a.updated_at DESC"
    )
    params: list[object] = [existing_kind]
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _body_snapshot(row: sqlite3.Row) -> tuple[str, str] | None:
    body_html = str(row["body_html"] or "").strip()
    if body_html:
        return body_html, "text/html; charset=utf-8"
    raw = str(row["raw_content"] or row["content"] or "").strip()
    if not raw:
        return None
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head>"
        "<body><pre>" + _escape(raw) + "</pre></body></html>",
        "text/html; charset=utf-8",
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填公告原文归档")
    parser.add_argument("--db", default=str(Path("data") / "news.db"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fetch-html", action="store_true", help="重抓原始 HTML（慢）")
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args(argv)

    conn = news_crawler.init_db(args.db)
    conn.row_factory = sqlite3.Row
    kind = article_archive.KIND_BODY_HTML
    try:
        rows = _candidates(
            conn, kind=kind, fetch_html=args.fetch_html, limit=args.limit
        )
        print(f"候选 {len(rows)} 条（{'raw_html' if args.fetch_html else 'body_html'}）")
        archived = 0
        skipped = 0
        failed = 0
        for index, row in enumerate(rows, 1):
            article_id = str(row["article_id"])
            source = str(row["source"])
            url = str(row["url"])
            payload: str | None = None
            payload_kind = kind
            content_type = "text/html; charset=utf-8"
            if args.fetch_html:
                try:
                    html = fetch_html(url)
                except Exception as exc:
                    print(f"[{index}/{len(rows)}] 抓取失败 {url}: {exc}")
                    html = ""
                if html:
                    payload = html
                    payload_kind = article_archive.KIND_RAW_HTML
                if args.delay > 0:
                    time.sleep(args.delay)
            if payload is None:
                snapshot = _body_snapshot(row)
                if snapshot is None:
                    skipped += 1
                    continue
                payload, content_type = snapshot
            if args.dry_run:
                print(f"[dry-run] {article_id} {payload_kind} {url}")
                archived += 1
                continue
            try:
                result = article_archive.archive_article(
                    conn,
                    article_id=article_id,
                    source=source,
                    url=url,
                    payload=payload,
                    kind=payload_kind,
                    content_type=content_type,
                )
                archived += 1
                if result:
                    print(f"[{index}/{len(rows)}] 已归档 {article_id} {payload_kind}")
            except Exception as exc:
                failed += 1
                print(f"[{index}/{len(rows)}] 归档失败 {article_id}: {exc}")
        print(
            f"完成：归档 {archived}，跳过 {skipped}，失败 {failed}"
            + ("（dry-run 未写盘）" if args.dry_run else "")
        )
        return 0 if failed == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
