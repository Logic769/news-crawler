"""回填 articles.raw_content：重抓 v4 之前入库的文章，补齐保留段落换行的原始正文。

content 列保持 clean_text() 归一化后的单行文本不变，只写 raw_content，
因此对下游检索与展示零影响；重抓失败的行原样保留，可稍后重跑。

失败条目会写入失败清单（默认与 --db 同目录的 backfill_failed.txt，TSV：source / url / error），
下次可用 --skip-file 传入该清单跳过这些死链，避免它们反复占用 --limit 名额。清单每次覆盖写，
但会保留本次 --skip-file 读入的条目（标注「上次失败，本次跳过」），因此多次运行不会把死链洗掉。
容器内数据库在 /app/data，因此默认落盘到 /app/data/backfill_failed.txt（随 ./data 挂载保留）。

宿主机（数据库在 ./data/news.db）：
    python -X utf8 backfill_raw_content.py --db data/news.db --dry-run
    python -X utf8 backfill_raw_content.py --db data/news.db

容器内（数据库在 /app/data/news.db，必须显式传 --db）：
    docker compose run --rm --entrypoint python crawler -X utf8 backfill_raw_content.py --db /app/data/news.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from gne import GeneralNewsExtractor
from news_crawler import DEFAULT_DB_FILE, SCHEMA_VERSION, init_db
from url_extract import close_thread_browser, extract_article

# 只有 ok / unknown_date 会写入 raw_content：old 分支刻意不写（见 process_entry），
# error 行本来就没有正文，回填它们只会浪费请求。
BACKFILL_STATUSES = ("ok", "unknown_date")

_UPDATE_SQL = (
    "UPDATE articles SET raw_content = ? "
    "WHERE source = ? AND url = ? AND (raw_content IS NULL OR raw_content = '')"
)

Failure = tuple[str, str, str]  # (source, url, error)


def find_candidates(
    conn: sqlite3.Connection,
    source: str,
    limit: int,
    skip: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """列出 raw_content 为空、且本应写入 raw_content 的文章。

    skip 来自上一次运行的失败清单：死链的 raw_content 一直为空，
    不排除的话每次运行都会重新占用 --limit 名额。
    """
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
    sql += " ORDER BY publish_time DESC, source, url"
    # skip 为空时可以把 LIMIT 下推到 SQL，避免大库把全部候选读进内存；
    # skip 非空时必须在 Python 侧截断，否则被排除的行也会占用名额。
    if limit > 0 and not skip:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [(str(row[0]), str(row[1])) for row in conn.execute(sql, params)]
    if skip:
        rows = [row for row in rows if row not in skip]
    if limit > 0:
        rows = rows[:limit]
    return rows


def load_skip_list(path: Path) -> set[tuple[str, str]]:
    """读取失败清单（TSV：source / url / error），返回要跳过的 (source, url)。"""
    skipped: set[tuple[str, str]] = set()
    if not path.is_file():
        return skipped
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            skipped.add((parts[0], parts[1]))
    return skipped


def _clean_tsv_field(value: str) -> str:
    """把任意文本压成单行，避免 TAB / 换行破坏 TSV 的列结构。"""
    return " ".join(value.replace("\t", " ").splitlines())


def _clean_error(error: str) -> str:
    """把异常文本压成单行，避免破坏 TSV 的列结构。"""
    return _clean_tsv_field(error)


def write_failed_list(path: Path, failures: list[Failure]) -> None:
    """把失败明细写成 TSV，供 --skip-file 复用。"""
    lines = ["# source\turl\terror"]
    lines.extend(
        f"{_clean_tsv_field(source)}\t{_clean_tsv_field(url)}\t{_clean_error(error)}"
        for source, url, error in failures
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def backfill(
    conn: sqlite3.Connection,
    candidates: list[tuple[str, str]],
    *,
    delay: float,
    allow_browser: bool,
    dry_run: bool,
) -> tuple[int, int, int, list[Failure]]:
    """逐条重抓并写回 raw_content。

    返回 (成功数, 失败数, 跳过数, 失败明细)。跳过指 UPDATE 未命中：
    通常是另一个进程已经回填，或行已被删除，不能与抓取失败混为一谈。
    """
    if dry_run or not candidates:
        return 0, 0, 0, []

    # 单线程串行复用同一实例是安全的（跨线程共享才会出 math domain error）。
    extractor = GeneralNewsExtractor()
    updated = 0
    failed = 0
    skipped = 0
    failures: list[Failure] = []
    try:
        for index, (source, url) in enumerate(candidates):
            if index and delay > 0:
                time.sleep(delay)
            try:
                result = extract_article(url, extractor, allow_browser=allow_browser)
                raw_content = str((result or {}).get("content") or "").strip()
            except Exception as exc:  # 单条失败不能中止整批
                failed += 1
                failures.append((source, url, str(exc)))
                print(f"  [失败] {source} {url}：{exc}")
                continue
            if not raw_content:
                failed += 1
                failures.append((source, url, "抽取结果没有正文"))
                print(f"  [失败] {source} {url}")
                continue
            cursor = conn.execute(_UPDATE_SQL, (raw_content, source, url))
            conn.commit()
            if cursor.rowcount:
                updated += 1
                print(f"  [回填 {len(raw_content)} 字] {source} {url}")
            else:
                skipped += 1
                print(f"  [跳过] {source} {url}（已被其他进程回填或条件不再满足）")
    finally:
        close_thread_browser()
    return updated, failed, skipped, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="回填 articles.raw_content：重抓历史行，补齐保留段落换行的原始正文"
    )
    parser.add_argument("--db", default=DEFAULT_DB_FILE)
    parser.add_argument("--source", default="", help="只回填指定站点（source 字段）")
    parser.add_argument(
        "--limit", type=int, default=0, help="本次最多尝试多少条（跨站点全局计数，0 为不限制）"
    )
    parser.add_argument(
        "--delay", type=float, default=0.5, help="两次请求之间的等待秒数，默认 0.5"
    )
    parser.add_argument(
        "--allow-browser",
        action="store_true",
        help="直接抓取失败时允许回退本机 Chrome 渲染（默认关闭）",
    )
    parser.add_argument(
        "--failed-list",
        default=None,
        help="失败清单输出路径（TSV：source / url / error），默认与 --db 同目录的 backfill_failed.txt",
    )
    parser.add_argument(
        "--skip-file",
        default="",
        help="跳过清单路径（通常是上一次的 --failed-list），避免死链重复占用名额",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计候选数量，不抓取（v3 旧库仍会升级表结构）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        print("--limit 不能为负数（0 表示不限制）", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    if not db_path.is_file():
        # init_db 会建新库，写错路径就静默变成「0 条候选」，因此先拦住。
        print(f"找不到数据库：{db_path}（回填只处理已有库，不会新建）", file=sys.stderr)
        return 2

    skip: set[tuple[str, str]] = set()
    if args.skip_file:
        skip_path = Path(args.skip_file)
        if not skip_path.is_file():
            print(f"找不到跳过清单：{skip_path}", file=sys.stderr)
            return 2
        skip = load_skip_list(skip_path)

    if args.dry_run:
        # dry-run 不抓取，但 init_db 仍会把 v3 库迁移到 v4 结构，这里提前说明。
        try:
            probe = sqlite3.connect(db_path)
            try:
                version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            finally:
                probe.close()
        except sqlite3.Error as exc:
            print(f"无法读取数据库版本：{db_path}（{exc}）", file=sys.stderr)
            return 2
        if version != SCHEMA_VERSION:
            print(
                f"注意：dry-run 仍会把库结构从 v{version} 迁移到 v{SCHEMA_VERSION}"
                "（只做库结构升级，含 article_id 回填，不改任何正文）。"
            )

    conn = init_db(str(db_path))
    try:
        candidates = find_candidates(conn, args.source, args.limit, skip)
        statuses = ", ".join(BACKFILL_STATUSES)
        print(f"候选 {len(candidates)} 条（status in {statuses} 且 raw_content 为空）")
        if skip:
            print(f"已跳过清单中的 {len(skip)} 条")
        updated, failed, skipped, failures = backfill(
            conn,
            candidates,
            delay=args.delay,
            allow_browser=args.allow_browser,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if args.dry_run:
        print("dry-run：未抓取，未写入任何正文。")
        return 0
    failed_path = Path(args.failed_list) if args.failed_list else db_path.parent / "backfill_failed.txt"
    if failed_path.resolve() == db_path.resolve():
        print("--failed-list 不能指向数据库文件本身", file=sys.stderr)
        return 2
    if failed_path.is_dir():
        print(f"--failed-list 是目录：{failed_path}", file=sys.stderr)
        return 2
    # 每次运行覆盖写，但保留本次 --skip-file 中的条目（它们仍是已知死链，只是这次没轮到），
    # 否则第二次运行会清空清单，第三次又把死链重新抓一遍。
    known = {(source, url) for source, url, _ in failures}
    preserved: list[Failure] = [
        (source, url, "上次失败，本次跳过") for source, url in sorted(skip) if (source, url) not in known
    ]
    write_failed_list(failed_path, failures + preserved)
    if failures:
        print(f"失败清单已写入：{failed_path}（下次可用 --skip-file 跳过这些条目）")
    print(f"完成：回填 {updated} 条，失败 {failed} 条，跳过 {skipped} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
