"""回填 articles.raw_content：重抓 v4 之前入库的文章，补齐保留段落换行的原始正文。

content 列保持 clean_text() 归一化后的单行文本不变，只写 raw_content，
因此对下游检索与展示零影响；重抓失败的行原样保留，可稍后重跑。

宿主机（数据库在 ./data/news.db）：
    python -X utf8 backfill_raw_content.py --db data/news.db --dry-run
    python -X utf8 backfill_raw_content.py --db data/news.db

容器内（数据库在 /app/data/news.db）：
    docker compose run --rm --entrypoint python crawler -X utf8 backfill_raw_content.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from gne import GeneralNewsExtractor
from news_crawler import DEFAULT_DB_FILE, init_db
from url_extract import close_thread_browser, extract_article

# 只有 ok / unknown_date 会写入 raw_content：old 分支刻意不写（见 process_entry），
# error 行本来就没有正文，回填它们只会浪费请求。
BACKFILL_STATUSES = ("ok", "unknown_date")

_UPDATE_SQL = (
    "UPDATE articles SET raw_content = ? "
    "WHERE source = ? AND url = ? AND (raw_content IS NULL OR raw_content = '')"
)


def find_candidates(
    conn: sqlite3.Connection, source: str, limit: int
) -> list[tuple[str, str]]:
    """列出 raw_content 为空、且本应写入 raw_content 的文章。"""
    placeholders = ", ".join("?" * len(BACKFILL_STATUSES))
    sql = (
        "SELECT source, url FROM articles "
        "WHERE (raw_content IS NULL OR raw_content = '') "
        f"AND status IN ({placeholders})"
    )
    params: list[object] = list(BACKFILL_STATUSES)
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY publish_time DESC, url"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return [(str(row[0]), str(row[1])) for row in conn.execute(sql, params)]


def backfill(
    conn: sqlite3.Connection,
    candidates: list[tuple[str, str]],
    *,
    delay: float,
    allow_browser: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """逐条重抓并写回 raw_content，返回 (成功数, 失败数)。"""
    if dry_run or not candidates:
        return 0, 0

    # 单线程串行复用同一实例是安全的（跨线程共享才会出 math domain error）。
    extractor = GeneralNewsExtractor()
    updated = 0
    failed = 0
    try:
        for index, (source, url) in enumerate(candidates):
            if index and delay > 0:
                time.sleep(delay)
            result = extract_article(url, extractor, allow_browser=allow_browser)
            raw_content = str((result or {}).get("content") or "").strip()
            if not raw_content:
                failed += 1
                print(f"  [失败] {source} {url}")
                continue
            cursor = conn.execute(_UPDATE_SQL, (raw_content, source, url))
            conn.commit()
            if cursor.rowcount:
                updated += 1
                print(f"  [回填 {len(raw_content)} 字] {source} {url}")
            else:
                failed += 1
                print(f"  [跳过] {source} {url}")
    finally:
        close_thread_browser()
    return updated, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="回填 articles.raw_content：重抓历史行，补齐保留段落换行的原始正文"
    )
    parser.add_argument("--db", default=DEFAULT_DB_FILE)
    parser.add_argument("--source", default="", help="只回填指定站点（source 字段）")
    parser.add_argument("--limit", type=int, default=0, help="最多回填多少条（0 为不限制）")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="两次请求之间的等待秒数，默认 0.5"
    )
    parser.add_argument(
        "--allow-browser",
        action="store_true",
        help="直接抓取失败时允许回退本机 Chrome 渲染（默认关闭）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计候选数量，不抓取")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    if not db_path.is_file():
        # init_db 会建新库，写错路径就静默变成「0 条候选」，因此先拦住。
        print(f"找不到数据库：{db_path}（回填只处理已有库，不会新建）", file=sys.stderr)
        return 2

    conn = init_db(str(db_path))
    try:
        candidates = find_candidates(conn, args.source, args.limit)
        statuses = ", ".join(BACKFILL_STATUSES)
        print(f"候选 {len(candidates)} 条（status in {statuses} 且 raw_content 为空）")
        updated, failed = backfill(
            conn,
            candidates,
            delay=args.delay,
            allow_browser=args.allow_browser,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if args.dry_run:
        print("dry-run：未抓取，未写入。")
        return 0
    print(f"完成：回填 {updated} 条，失败 {failed} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
