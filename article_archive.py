"""公告原文归档：原始 HTML / 接口响应的可追溯快照。

设计约束（对应立项书「公告原文单独备份存档、可追溯」）：

- 文件不落在 SQLite blob 里：每条快照写成 gzip 文件，路径为
  {db目录}/archive/{article_id前两位}/{article_id}/{时间}_{kind}.gz；
- 同目录写一份 .meta.json sidecar，便于不依赖数据库也能核对来源；
- article_archives 表只存索引（路径、sha256、大小、内容类型、归档时间）；
- 同一条文章同一份内容重复抓取不重复落盘（按 sha256 去重）；
- 保留一年：过期快照移动到 archive_trash/{日期}/，不硬删除；
- 归档失败不能影响抓取主流程：调用方按 best-effort 处理并记日志。
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import zlib
from pathlib import Path
from typing import Any

LOG = logging.getLogger("article_archive")

ARCHIVE_DIR_NAME = "archive"
TRASH_DIR_NAME = "archive_trash"
DEFAULT_RETENTION_DAYS = 365
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
KIND_RAW_HTML = "raw_html"
KIND_API_RESPONSE = "api_response"
KIND_BODY_HTML = "body_html"
VALID_KINDS = frozenset({KIND_RAW_HTML, KIND_API_RESPONSE, KIND_BODY_HTML})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS article_archives (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   TEXT NOT NULL,
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    kind         TEXT NOT NULL,
    path         TEXT NOT NULL UNIQUE,
    sha256       TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_article_archives_article
    ON article_archives(article_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_archives_created
    ON article_archives(created_at);
"""


class ArticleArchiveError(RuntimeError):
    """归档基础设施错误（路径不可用、快照不存在等）。"""


def archive_root_for_db(db_path: str | Path) -> Path:
    """归档根目录固定为数据库文件同级的 archive/。"""
    return Path(db_path).expanduser().resolve().parent / ARCHIVE_DIR_NAME


def trash_root_for_db(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / TRASH_DIR_NAME


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _db_path_from_conn(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        # PRAGMA database_list: seq, name, file
        if str(row[1]) == "main" and row[2]:
            return str(row[2])
    raise ArticleArchiveError("无法从数据库连接解析归档目录（内存库或未落盘数据库）")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(stamp: dt.datetime) -> str:
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _file_stamp(stamp: dt.datetime) -> str:
    # 文件名可排序，微秒避免同秒内多个快照重名。
    return stamp.strftime("%Y%m%dT%H%M%S%fZ")


def _safe_component(value: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in str(value) if ch.isalnum() or ch in "-_")
    return cleaned or fallback


_MAX_FILENAME_SUFFIX = 1000


def _write_exclusive(target: Path, data: bytes) -> None:
    """原子地把 data 写到 target，目标已存在时抛 FileExistsError。

    不能用「先 Path.exists() 再 os.replace」：exists 与 replace 之间存在 TOCTOU 窗口，
    多进程/多线程在同一微秒归档同一 article_id 时都会通过检查，后到者的 os.replace
    直接覆盖先到者的文件。O_CREAT|O_EXCL 把「检查 + 创建」交给内核，是原子的。
    """
    # Windows 上文本模式会做 CRLF 转换，破坏 gzip 二进制；O_BINARY 在 POSIX 上不存在。
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(str(target), flags)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        # 写了一半的文件不能留：它会让后续 exists 检查以为名字被占用。
        try:
            target.unlink()
        except OSError:
            LOG.warning("清理写入失败的归档文件失败: %s", target)
        raise


def _claim_archive_filename(
    article_dir: Path, stamp: dt.datetime, kind: str, data: bytes
) -> tuple[str, Path]:
    """原子地占住一个归档文件名并写入 data，返回 (文件名, 完整路径)。

    名字冲突（含并发竞争）一律由 FileExistsError 触发：递增序号重试，而不是覆盖。
    这样「created」就等价于「本次真的新建了这个文件」，清理分支才敢删。
    """
    base = f"{_file_stamp(stamp)}_{_safe_component(kind, 'snapshot')}"
    meta_conflict = False
    for suffix in range(_MAX_FILENAME_SUFFIX + 1):
        candidate = f"{base}.gz" if suffix == 0 else f"{base}-{suffix}.gz"
        target = article_dir / candidate
        # sidecar 已存在说明这组名字被别的归档占用了（它先写了 meta），跳过。
        if (article_dir / f"{candidate}.meta.json").exists():
            meta_conflict = True
            continue
        try:
            _write_exclusive(target, data)
        except FileExistsError:
            meta_conflict = True
            continue
        return candidate, target
    del meta_conflict  # 只为可读性保留，真正的判断在循环里
    raise ArticleArchiveError("归档文件名重复过多")


_ARTICLE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _validate_article_id(value: str) -> str:
    """校验 article_id 可以安全地作为单段目录名使用。

    article_id 直接参与归档路径拼接（archive/{前两位}/{article_id}/），来自外部
    数据，必须挡掉路径分隔符、`..` 和控制字符，否则可以写到归档根目录之外。
    正常取值是 sha256 前 32 位十六进制，这里放宽到字母数字与 `-_` 以兼容外部
    系统自带的 ID，但不接受任何其它字符。
    """
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ArticleArchiveError("article_id 为空，无法归档")
    if not _ARTICLE_ID_PATTERN.fullmatch(cleaned):
        raise ArticleArchiveError(
            f"article_id 含非法字符，无法归档: {cleaned[:64]!r}"
        )
    return cleaned


def _row_to_dict(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return {
        "id": row[0],
        "article_id": row[1],
        "source": row[2],
        "url": row[3],
        "kind": row[4],
        "path": row[5],
        "sha256": row[6],
        "size_bytes": row[7],
        "content_type": row[8],
        "created_at": row[9],
    }


def _find_duplicate(
    conn: sqlite3.Connection, article_id: str, sha256: str, kind: str, root: Path
) -> dict[str, Any] | None:
    # 必须带上 kind：同一篇文章的 raw_html 与 body_html 内容可能恰好相同，
    # 只按 (article_id, sha256) 复用会让后一种 kind 永远拿不到自己的归档行，
    # 而 backfill_archives 的候选 SQL 按 kind 过滤，会反复选出同一个死循环候选。
    row = conn.execute(
        "SELECT id, article_id, source, url, kind, path, sha256, size_bytes, "
        "content_type, created_at FROM article_archives "
        "WHERE article_id = ? AND sha256 = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (article_id, sha256, kind),
    ).fetchone()
    if row is None:
        return None
    item = _row_to_dict(row)
    candidate = (root / str(item["path"])).resolve()
    if candidate.is_file():
        return item
    return None


def archive_article(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    source: str,
    url: str,
    payload: str | bytes,
    kind: str,
    content_type: str = "text/html; charset=utf-8",
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """把一份抓取原文写入归档并登记索引。

    返回归档索引；同文章同内容已存在时直接复用。失败抛异常，由调用方决定
    是否降级为日志（抓取主流程应继续）。

    事务约定：函数只在本函数内部失败时回滚并删文件。若调用方把归档放进自己的
    外层事务并在之后 rollback，索引会回滚而磁盘文件留下——这属于调用方的责任，
    现有调用点（news_crawler.process_entry 在归档前已 commit，backfill_archives
    无显式外层事务）都不受影响。需要「同一事务内必须原子」时，改成先提交再归档：
    prune 只扫有索引的行，看不见没有索引的孤儿文件，指望它清理是错的。
    """
    article_id = _validate_article_id(article_id)
    if kind not in VALID_KINDS:
        raise ArticleArchiveError(f"未知归档类型: {kind}")
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    root = archive_root_for_db(db_path or _db_path_from_conn(conn))
    duplicate = _find_duplicate(conn, article_id, digest, kind, root)
    if duplicate is not None:
        return duplicate

    stamp = _now()
    article_dir = root / article_id[:2] / article_id
    # 纵深防御：字符集校验之外再确认最终目录没跑出归档根（root 可能含符号链接，
    # 归档根下的分层目录也可能被替换成 junction）。检查跑两次：mkdir 之前一次挡住
    # 「已经存在的 junction」，mkdir 之后再一次挡住真实路径，后者才看得见刚建出来的
    # 目录链。之所以敢在 mkdir 之前就 resolve，是因为 resolve(strict=False) 对不存在的
    # 路径只做字符串展开、不要求路径存在。
    root_resolved = root.resolve()
    # 越界检查必须放在 mkdir 之前：归档根下某一层若被替换成 junction/符号链接，
    # 先 mkdir 会在根外真的建出空目录，再被这里拒绝，留下没人认领的目录。
    # 目录不存在时 resolve(strict=False) 是纯字符串展开，不需要目录已存在，
    # 所以这里的判断不会因为「目录还没建」而失真；建完之后再核一次真实路径，
    # 挡住「检查通过后组件才被替换」的竞态。
    if not article_dir.resolve().is_relative_to(root_resolved):
        raise ArticleArchiveError(f"归档路径越界: {article_id[:64]!r}")
    article_dir.mkdir(parents=True, exist_ok=True)
    if not article_dir.resolve().is_relative_to(root_resolved):
        raise ArticleArchiveError(f"归档路径越界: {article_id[:64]!r}")
    meta = {
        "article_id": article_id,
        "source": source,
        "url": url,
        "kind": kind,
        "sha256": digest,
        "size_bytes": len(raw),
        "compressed_size": 0,
        "content_type": content_type,
        "created_at": _iso(stamp),
    }

    # 写盘与写索引必须当成一件事：任何一步失败都要把本次已经落盘的文件清掉。
    #
    # 原来是「先落盘、再 try: INSERT」，try 从 INSERT 才开始，于是 gz 写失败或
    # meta 写失败会留下没人登记的归档文件——prune 只扫有索引的行，这些孤儿永远
    # 不会被回收。所以 try 的起点必须提前到第一次写盘之前。
    #
    # 用 SAVEPOINT 而不是 rollback()：调用方（save_article）复用同一个
    # connection，可能带着自己尚未提交的写入，整连接回滚会把它一起丢掉。成功路径
    # 同理不能 conn.commit()——那会把调用方的事务替它提交掉。SAVEPOINT 只作用于
    # 本函数这一段，进出都不改变外层事务的状态。
    created = False
    savepoint = "article_archive_insert"
    data_path: Path | None = None
    meta_path: Path | None = None
    try:
        compressed = gzip.compress(raw, mtime=0)
        meta["compressed_size"] = len(compressed)
        # 原子占名 + 写入：并发下后到者拿到 FileExistsError 自动换序号，
        # 绝不会覆盖别人已经落盘的文件。
        filename, data_path = _claim_archive_filename(article_dir, stamp, kind, compressed)
        relative = data_path.relative_to(root).as_posix()
        meta_path = data_path.with_name(data_path.name + ".meta.json")
        created = True
        # meta 写失败时不要在这里自己删文件：write_text 可能已经落下一半内容，
        # 只删主文件会留下一个半成品 sidecar。交给外层统一清理 data_path 与
        # meta_path 两条路径（它按 exists() 逐个删，半成品也在覆盖范围内）。
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            cursor = conn.execute(
                "INSERT INTO article_archives "
                "(article_id, source, url, kind, path, sha256, size_bytes, content_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    article_id,
                    source,
                    url,
                    kind,
                    relative,
                    digest,
                    len(raw),
                    content_type,
                    meta["created_at"],
                ),
            )
            conn.execute(f"RELEASE {savepoint}")
        except BaseException:
            # 只撤销这一段；外层事务（调用方未提交的写入）原样保留。
            try:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            except sqlite3.Error:  # pragma: no cover - 连接已坏时无补救手段
                LOG.warning("回滚归档索引保存点失败: %s", article_id)
            raise
    except BaseException:
        # 只删「本次真的创建出来的」文件。
        #
        # created 现在等价于「_claim_archive_filename 用 O_CREAT|O_EXCL 新建成功」，
        # 因此这里的 unlink 不可能误删别人的归档：并发者拿到的是 FileExistsError，
        # 会自动换序号，根本不会走到这个分支去覆盖既存文件。
        if created:
            for path in (data_path, meta_path):
                if path is None:
                    continue
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    LOG.warning("清理未登记的归档文件失败: %s", path)
        raise
    columns = (
        "article_id",
        "source",
        "url",
        "kind",
        "sha256",
        "size_bytes",
        "content_type",
        "created_at",
    )
    return {
        "id": int(cursor.lastrowid or 0),
        **{key: meta[key] for key in columns},
        "path": relative,
    }


def list_archives(
    db_path: str | Path, article_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, article_id, source, url, kind, path, sha256, size_bytes, "
            "content_type, created_at FROM article_archives "
            "WHERE article_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (article_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def get_archive(db_path: str | Path, archive_id: int) -> tuple[dict[str, Any], bytes]:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, article_id, source, url, kind, path, sha256, size_bytes, "
            "content_type, created_at FROM article_archives WHERE id = ?",
            (int(archive_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ArticleArchiveError("归档不存在")
    item = _row_to_dict(row)
    root = archive_root_for_db(db_path).resolve()
    target = (root / str(item["path"])).resolve()
    if not target.is_relative_to(root):
        raise ArticleArchiveError("归档路径越界")
    if not target.is_file():
        raise ArticleArchiveError("归档文件缺失")
    try:
        with gzip.open(target, "rb") as handle:
            data = handle.read(MAX_ARCHIVE_BYTES + 1)
    except PermissionError:
        # 权限不足不是「归档损坏」：把它转成 404 会把排查方向引向数据损坏，
        # 真实原因是文件/挂载点不可读。原样向上抛，让路由给出 5xx 并留 traceback。
        raise
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        # 归档被截断或数据损坏（磁盘满、拷贝中断、手工改过）时 gzip 会抛
        # EOFError/BadGzipFile，deflate 流中段损坏则抛 zlib.error（它是 Exception
        # 而不是 OSError 的子类，漏掉就会变成 HTTP 500）。归档损坏是「这条归档
        # 不可用」，属于数据问题而非服务错误，统一转成 ArticleArchiveError 让路由
        # 返回 404 并带上可读原因。
        raise ArticleArchiveError(f"归档文件损坏: {target.name}") from exc
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArticleArchiveError("归档文件超过读取上限")
    return item, data


def prune_expired_archives(
    db_path: str | Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """把超过保留期的快照移动到 archive_trash/，再删索引。

    返回 {"scanned": n, "moved": n, "missing": n, "failed": n}。
    """
    days = max(1, int(retention_days))
    stamp = now or _now()
    cutoff = stamp - dt.timedelta(days=days)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    root = archive_root_for_db(db_path).resolve()
    trash_root = trash_root_for_db(db_path).resolve() / stamp.strftime("%Y%m%d")
    counts = {"scanned": 0, "moved": 0, "missing": 0, "failed": 0}
    try:
        rows = conn.execute(
            "SELECT id, path, created_at FROM article_archives "
            "WHERE created_at < ? ORDER BY created_at",
            (_iso(cutoff),),
        ).fetchall()
        counts["scanned"] = len(rows)
        for row in rows:
            archive_id = int(row["id"])
            relative = str(row["path"])
            source = (root / relative).resolve()
            if not source.is_relative_to(root):
                LOG.warning("归档路径越界，跳过: %s", relative)
                counts["failed"] += 1
                continue
            if not source.is_file():
                counts["missing"] += 1
                LOG.warning("归档文件缺失，保留索引待人工处理: %s", source)
                continue
            if dry_run:
                counts["moved"] += 1
                continue
            target = trash_root / relative
            sidecar = source.with_name(source.name + ".meta.json")
            side_target = target.with_name(target.name + ".meta.json")
            moved: list[tuple[Path, Path]] = []
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved.append((source, target))
                if sidecar.is_file():
                    side_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(sidecar), str(side_target))
                    moved.append((sidecar, side_target))
                conn.execute("DELETE FROM article_archives WHERE id = ?", (archive_id,))
                conn.commit()
                counts["moved"] += 1
            except (OSError, sqlite3.Error) as exc:
                # 文件已经搬走但索引没删掉，就会留下指向 archive_trash 的悬空索引。
                # 文件系统不支持事务，这里用补偿操作把文件搬回原位，让库与盘保持一致。
                try:
                    conn.rollback()
                except sqlite3.Error:  # pragma: no cover - 连接已坏时无补救手段
                    LOG.warning("回滚裁剪事务失败: %s", archive_id)
                # 回滚顺序：主文件（gz）优先搬回，sidecar 其次。
                #
                # moved 是按「先 gz 后 meta」记录的，直觉上的 reversed(moved) 会先搬
                # meta —— 一旦 gz 搬回失败，archive/ 里就留下一个孤立的 .meta.json，
                # 而真正要恢复的是主文件。先 gz 后 meta 时，最坏情况只是 archive/
                # 缺一个 sidecar（可重建），不会留下指向不存在内容的元数据。
                restore_failed = False
                for original, moved_to in sorted(
                    moved, key=lambda pair: pair[0].name.endswith(".meta.json")
                ):
                    try:
                        original.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(moved_to), str(original))
                    except OSError:
                        restore_failed = True
                        LOG.error("回滚已移动的归档文件失败: %s", moved_to)
                if restore_failed:
                    LOG.error(
                        "过期归档裁剪失败且文件未能搬回，索引已保留，需人工核对: %s", source
                    )
                else:
                    LOG.warning("裁剪过期归档失败，已回滚文件移动，保留索引: %s (%s)", source, exc)
                counts["failed"] += 1
    finally:
        conn.close()
    return counts


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="公告原文归档维护")
    parser.add_argument("--db", default="news.db")
    parser.add_argument("--prune", action="store_true", help="移动超过保留期的归档到 archive_trash")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.prune:
        parser.print_help()
        return 2
    result = prune_expired_archives(
        args.db,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
