"""公告原文归档的离线回归用例。"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from typing import Any, cast
from unittest import mock

import article_archive
import news_crawler


class ArticleArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = str(self.root / "news.db")
        self.conn = news_crawler.init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _save(self, payload: str, *, url: str = "https://example.com/a") -> str:
        return news_crawler.save_article(
            self.conn,
            "example",
            url,
            title="示例公告",
            publish_time="2026-11-16 10:00:00",
            timezone="Asia/Shanghai",
            content="正文",
            raw_content="正文",
            status="ok",
            archive_payload=payload,
            archive_kind=article_archive.KIND_RAW_HTML,
        )

    def test_archive_writes_gzip_and_index(self) -> None:
        article_id = self._save("<html>原文</html>")
        rows = article_archive.list_archives(self.db_path, article_id)
        self.assertEqual(1, len(rows))
        item = rows[0]
        self.assertEqual(article_archive.KIND_RAW_HTML, item["kind"])
        self.assertEqual(len("<html>原文</html>".encode()), item["size_bytes"])
        data_path = article_archive.archive_root_for_db(self.db_path) / item["path"]
        self.assertTrue(data_path.is_file())
        self.assertEqual("<html>原文</html>", gzip.decompress(data_path.read_bytes()).decode("utf-8"))
        meta_path = data_path.with_name(data_path.name + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(article_id, meta["article_id"])
        self.assertEqual("https://example.com/a", meta["url"])

    def test_same_content_is_not_duplicated(self) -> None:
        article_id = self._save("<html>same</html>")
        first = article_archive.list_archives(self.db_path, article_id)[0]
        self._save("<html>same</html>")
        rows = article_archive.list_archives(self.db_path, article_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(first["id"], rows[0]["id"])

    def test_different_content_keeps_both_snapshots(self) -> None:
        """同文章不同内容必须落成两份文件。

        必须把 _now 钉在同一微秒：否则两次归档天然落在不同文件名上，
        「_claim_archive_filename 直接返回 base.gz 覆盖」这种退化也照样通过。
        钉住时间后，第二份只能靠递增序号拿到 -1.gz，覆盖实现会撞 FileExistsError。
        """
        fixed = dt.datetime(2026, 11, 16, 10, 0, 0, 123456, tzinfo=dt.timezone.utc)
        original_now = article_archive._now
        article_archive._now = lambda: fixed
        try:
            article_id = self._save("<html>v1</html>")
            self._save("<html>v2</html>", url="https://example.com/a")
        finally:
            article_archive._now = original_now
        rows = article_archive.list_archives(self.db_path, article_id)
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({row["sha256"] for row in rows}))
        names = sorted(Path(str(row["path"])).name for row in rows)
        self.assertEqual(2, len(set(names)), names)
        self.assertEqual(1, sum(1 for name in names if "-1.gz" in name), names)
        for row in rows:
            target = article_archive.archive_root_for_db(self.db_path) / str(row["path"])
            self.assertTrue(target.is_file(), row["path"])
            self.assertEqual(
                row["sha256"],
                article_archive.hashlib.sha256(gzip.decompress(target.read_bytes())).hexdigest(),
            )

    def test_same_content_different_kind_keeps_own_record(self) -> None:
        """同内容不同 kind 不能复用：raw_html 与 body_html 撞库会让回填候选死循环。"""
        article_id = self._save("<html>same-bytes</html>")
        body = article_archive.archive_article(
            self.conn,
            article_id=article_id,
            source="example",
            url="https://example.com/a",
            payload="<html>same-bytes</html>",
            kind=article_archive.KIND_BODY_HTML,
            db_path=self.db_path,
        )
        self.assertIsNotNone(body)
        rows = article_archive.list_archives(self.db_path, article_id)
        self.assertEqual(
            {article_archive.KIND_RAW_HTML, article_archive.KIND_BODY_HTML},
            {str(row["kind"]) for row in rows},
        )

    def test_get_archive_returns_decompressed_bytes(self) -> None:
        article_id = self._save("<html>download</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        row, data = article_archive.get_archive(self.db_path, int(item["id"]))
        self.assertEqual(article_id, row["article_id"])
        self.assertEqual(b"<html>download</html>", data)

    def test_prune_moves_expired_snapshot_to_trash(self) -> None:
        article_id = self._save("<html>old</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        source = article_archive.archive_root_for_db(self.db_path) / item["path"]
        self.assertTrue(source.is_file())
        result = article_archive.prune_expired_archives(
            self.db_path,
            retention_days=365,
            now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=366),
        )
        self.assertEqual(1, result["moved"])
        self.assertEqual([], article_archive.list_archives(self.db_path, article_id))
        self.assertFalse(source.exists())
        trash = article_archive.trash_root_for_db(self.db_path)
        moved = list(trash.rglob("*.gz"))
        self.assertEqual(1, len(moved))
        self.assertEqual("<html>old</html>", gzip.decompress(moved[0].read_bytes()).decode("utf-8"))

    def test_prune_keeps_missing_file_index(self) -> None:
        article_id = self._save("<html>missing</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        source = article_archive.archive_root_for_db(self.db_path) / item["path"]
        source.unlink()
        result = article_archive.prune_expired_archives(
            self.db_path,
            retention_days=365,
            now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=366),
        )
        self.assertEqual(1, result["missing"])
        self.assertEqual(1, len(article_archive.list_archives(self.db_path, article_id)))

    def test_index_failure_rolls_back_so_later_commit_cannot_orphan(self) -> None:
        """索引写失败后必须回滚：否则调用方下一次 commit 会把脏索引提交上去，
        留下指向已删除文件的记录（save_article 复用同一个 connection）。"""

        class _InsertFailsOnce:
            """第一次 INSERT article_archives 模拟磁盘写失败，其余调用透传。

            归档现在用 SAVEPOINT 而不是 commit 界定边界，所以「写索引失败」要在
            INSERT 上模拟：commit 已经不该由 archive_article 调用了。
            """

            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn
                self.failed = False

            def __getattr__(self, name: str) -> Any:
                return getattr(self._conn, name)

            def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
                if not self.failed and "INSERT INTO article_archives" in sql:
                    self.failed = True
                    raise sqlite3.OperationalError("disk I/O error")
                return self._conn.execute(sql, *args, **kwargs)

            def commit(self) -> None:
                return self._conn.commit()

        # 先在同一个连接上留一条「调用方还没提交」的文章行：archive_article 的
        # SAVEPOINT 边界是否正确，就看这条行在失败后还在不在。
        self.conn.execute(
            "INSERT INTO articles (source, url, article_id, status) VALUES (?, ?, ?, ?)",
            (
                "example",
                "https://example.com/rollback",
                news_crawler.article_id_for("example", "https://example.com/rollback"),
                "ok",
            ),
        )
        proxy = _InsertFailsOnce(self.conn)
        with self.assertRaises(sqlite3.OperationalError):
            article_archive.archive_article(
                cast("sqlite3.Connection", proxy),
                article_id="a" * 32,
                source="example",
                url="https://example.com/rollback",
                payload="<html>rollback</html>",
                kind=article_archive.KIND_RAW_HTML,
            )
        # 关键断言：注入点就在调用方事务内部，失败后外层未提交的写入必须还原样
        # 留着、事务仍处于打开状态。若实现退回整连接 rollback() 或干脆 commit()，
        # 这两条会立刻挂掉（前者丢行，后者把脏行提交上去）。
        self.assertTrue(self.conn.in_transaction)
        row_count = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE source = ? AND url = ?",
            ("example", "https://example.com/rollback"),
        ).fetchone()[0]
        self.assertEqual(1, row_count)
        # 模拟调用方在同一连接上继续工作并提交。
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM article_archives WHERE article_id = ?", ("a" * 32,)
        ).fetchone()[0]
        self.assertEqual(0, count)
        archive_root = article_archive.archive_root_for_db(self.db_path)
        self.assertEqual([], list(archive_root.rglob("*.gz")))

    def test_prune_restores_file_when_index_delete_fails(self) -> None:
        """裁剪时 DELETE 失败：文件必须搬回原位，索引保留，不能出现库盘不一致。

        除了最终状态，还要断言恢复顺序。只检查「最后都回来了」的话，把排序从
        「gz 优先」改成「meta 优先」也照样通过，而那个顺序在最坏情况下会在
        archive/ 里留下指向不存在内容的孤立 .meta.json。
        """
        article_id = self._save("<html>prune-fail</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        source = article_archive.archive_root_for_db(self.db_path) / item["path"]
        self.assertTrue(source.is_file())
        self.conn.execute(
            "CREATE TRIGGER block_archive_delete BEFORE DELETE ON article_archives"
            " BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
        self.conn.commit()
        moves: list[str] = []
        real_move = article_archive.shutil.move

        def recording_move(src: Any, dst: Any) -> Any:
            moves.append(Path(str(dst)).name)
            return real_move(src, dst)

        article_archive.shutil.move = recording_move  # type: ignore[assignment]
        try:
            result = article_archive.prune_expired_archives(
                self.db_path,
                retention_days=365,
                now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=366),
            )
        finally:
            article_archive.shutil.move = real_move  # type: ignore[assignment]
        self.assertEqual(1, result["failed"])
        self.assertEqual(0, result["moved"])
        # 搬出去 gz→meta，搬回来也必须 gz→meta。
        self.assertEqual(4, len(moves), moves)
        self.assertTrue(moves[2].endswith(".gz"), moves)
        self.assertTrue(moves[3].endswith(".meta.json"), moves)
        # 文件必须回到原位置，而不是留在 archive_trash。
        self.assertTrue(source.is_file())
        self.assertEqual(
            "<html>prune-fail</html>", gzip.decompress(source.read_bytes()).decode("utf-8")
        )
        trash = article_archive.trash_root_for_db(self.db_path)
        self.assertEqual([], list(trash.rglob("*.gz")) if trash.exists() else [])
        # 索引仍然在，便于下一轮重试。
        self.assertEqual(1, len(article_archive.list_archives(self.db_path, article_id)))

    def test_archive_rejects_path_traversal_article_id(self) -> None:
        """article_id 参与路径拼接，必须挡掉分隔符与 .. 。"""
        for bad in ("../escape", "a/b", "..", "", "a\x00b", "a" * 129):
            with self.subTest(article_id=bad):
                with self.assertRaises(article_archive.ArticleArchiveError):
                    article_archive.archive_article(
                        self.conn,
                        article_id=bad,
                        source="example",
                        url="https://example.com/traversal",
                        payload="<html>x</html>",
                        kind=article_archive.KIND_RAW_HTML,
                    )
        archive_root = article_archive.archive_root_for_db(self.db_path)
        self.assertEqual([], list(archive_root.rglob("*.gz")) if archive_root.exists() else [])

    def test_attachment_header_is_latin1_safe_for_non_ascii_id(self) -> None:
        """非 ASCII 文件名不能直接进 latin-1 响应头，否则 Starlette 编码时报 500。"""
        from news_api.app import _attachment_disposition

        header = _attachment_disposition("公告abc_raw_html.html")
        self.assertIn("filename*=UTF-8''", header)
        header.encode("latin-1")  # 不抛异常才算合格
        quoted = header.split("filename*=UTF-8''", 1)[1]
        self.assertEqual("公告abc_raw_html.html", urllib.parse.unquote(quoted))
        # ASCII 兜底名不能含引号/反斜杠，且保持可读。
        fallback = header.split('filename="', 1)[1].split('"', 1)[0]
        self.assertTrue(fallback.startswith("__abc_raw_html"), fallback)
        self.assertTrue(fallback.endswith(".html"), fallback)
        self.assertTrue(fallback.isascii(), fallback)

    def test_attachment_fallback_name_is_unique_per_filename(self) -> None:
        """两个不同的中文名不能退化成同一个 ASCII 兜底名，否则下载会互相覆盖。"""
        from news_api.app import _attachment_disposition

        def fallback_of(name: str) -> str:
            header = _attachment_disposition(name)
            return header.split('filename="', 1)[1].split('"', 1)[0]

        first = fallback_of("公告abc_raw_html.html")
        second = fallback_of("通知abc_raw_html.html")
        self.assertNotEqual(first, second)
        # 纯 ASCII 名字不该被加上哈希后缀。
        self.assertEqual("plain.html", fallback_of("plain.html"))

    def test_get_archive_reports_corruption_as_archive_error(self) -> None:
        """归档被截断时必须转成 ArticleArchiveError，否则下载路由会给 500。

        下载路由只捕 ArticleArchiveError；gzip 遇到截断文件抛的是 EOFError，
        原样冒泡就是 HTTP 500 + traceback。
        """
        article_archive.archive_article(
            self.conn,
            article_id="corrupt01",
            source="example",
            url="https://example.com/corrupt",
            payload="<html>" + "x" * 500 + "</html>",
            kind=article_archive.KIND_RAW_HTML,
        )
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, path FROM article_archives ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        root = article_archive.archive_root_for_db(self.db_path)
        target = root / row["path"]
        raw = target.read_bytes()
        target.write_bytes(raw[: max(10, len(raw) // 2)])

        with self.assertRaises(article_archive.ArticleArchiveError) as ctx:
            article_archive.get_archive(self.db_path, int(row["id"]))
        self.assertIn("损坏", str(ctx.exception))

    def test_meta_write_failure_leaves_no_partial_sidecar(self) -> None:
        """meta 写到一半失败时，主文件与半成品 sidecar 都必须清掉。"""
        article_dir = article_archive.archive_root_for_db(self.db_path)
        real_write = Path.write_text

        def failing_write(self_path: Path, *args: Any, **kwargs: Any) -> Any:
            if self_path.name.endswith(".meta.json"):
                self_path.write_bytes(b"PARTIAL-META")
                raise OSError("disk full after partial meta")
            return real_write(self_path, *args, **kwargs)

        Path.write_text = failing_write  # type: ignore[assignment]
        try:
            with self.assertRaises(OSError):
                article_archive.archive_article(
                    self.conn,
                    article_id="c" * 32,
                    source="example",
                    url="https://example.com/meta",
                    payload="<html>meta</html>",
                    kind=article_archive.KIND_RAW_HTML,
                )
        finally:
            Path.write_text = real_write  # type: ignore[assignment]
        self.assertEqual([], list(article_dir.rglob("*.gz")) if article_dir.exists() else [])
        self.assertEqual(
            [], list(article_dir.rglob("*.meta.json")) if article_dir.exists() else []
        )

    def test_get_archive_reports_zlib_corruption_as_archive_error(self) -> None:
        """deflate 流中段损坏抛的是 zlib.error，不是 OSError 子类。

        漏掉它，下载路由就捕不到 ArticleArchiveError，真实损坏会变成 HTTP 500。
        """
        article_id = self._save("<html>" + "y" * 800 + "</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        target = article_archive.archive_root_for_db(self.db_path) / str(item["path"])
        raw = bytearray(target.read_bytes())
        # 第 10 字节落在 deflate 数据中段：改这里解码会报 invalid code lengths set。
        raw[10] ^= 0xFF
        target.write_bytes(bytes(raw))

        with self.assertRaises(article_archive.ArticleArchiveError) as ctx:
            article_archive.get_archive(self.db_path, int(item["id"]))
        self.assertIn("损坏", str(ctx.exception))

    def test_get_archive_does_not_report_permission_error_as_corruption(self) -> None:
        """PermissionError 是 OSError 子类，不能被当成「归档损坏」转成 404。"""
        article_id = self._save("<html>perm</html>")
        item = article_archive.list_archives(self.db_path, article_id)[0]
        real_open = article_archive.gzip.open

        def failing_open(*args: Any, **kwargs: Any) -> Any:
            raise PermissionError(13, "Permission denied")

        article_archive.gzip.open = failing_open
        try:
            with self.assertRaises(PermissionError):
                article_archive.get_archive(self.db_path, int(item["id"]))
        finally:
            article_archive.gzip.open = real_open

    def test_schema_table_created_on_init(self) -> None:
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("article_archives", tables)


class ArticleArchiveProcessEntryTests(unittest.TestCase):
    """抓取主流程真的会把页面交给归档（而不是只有 API 层能归档）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = str(self.root / "news.db")
        self.conn = news_crawler.init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _entry(self, *, url: str = "https://example.com/detail/1") -> dict[str, Any]:
        return {
            "title": "2026年度项目申报指南",
            "url": url,
            "publish_text": "2026-11-16",
            "deadline_text": "2026-11-30 16:30:00",
            "deadline": news_crawler.parse_datetime(
                "2026-11-30 16:30:00", None, "Asia/Shanghai"
            ),
            "project_status": "申报中",
        }

    def _source(self, **extra: Any) -> dict[str, Any]:
        source: dict[str, Any] = {
            "name": "proj_site",
            "listing_type": "project_application",
            "timezone": "Asia/Shanghai",
            "content_required": False,
        }
        source.update(extra)
        return source

    def test_project_no_content_archives_fetched_html(self) -> None:
        """抽不出正文正是最需要回看原始页面的场景，必须留档。"""
        conn = self.conn
        cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
        assert cutoff is not None
        page = "<html><body><div id='app'></div></body></html>"
        with mock.patch.object(
            news_crawler, "fetch_article_html", return_value=(page, False)
        ), mock.patch.object(news_crawler, "extract_article", return_value=None):
            status = news_crawler.process_entry(
                conn, self._source(content_required=True), self._entry(), cutoff, force=False
            )
        self.assertEqual("no_content", status)
        row = conn.execute("SELECT article_id FROM articles").fetchone()
        self.assertIsNotNone(row)
        rows = article_archive.list_archives(self.db_path, str(row[0]))
        self.assertEqual([article_archive.KIND_RAW_HTML], [str(r["kind"]) for r in rows])
        stored = article_archive.archive_root_for_db(self.db_path) / str(rows[0]["path"])
        self.assertEqual(page, gzip.decompress(stored.read_bytes()).decode("utf-8"))

    def test_project_old_entry_before_fetch_has_no_archive(self) -> None:
        """截止时间早于窗口时在抓取前就返回 old，不能凭空造出快照。"""
        conn = self.conn
        cutoff = news_crawler.parse_datetime("2026-12-31", None, "Asia/Shanghai")
        assert cutoff is not None
        with mock.patch.object(
            news_crawler, "fetch_article_html", side_effect=AssertionError("不应抓取")
        ):
            status = news_crawler.process_entry(
                conn, self._source(content_required=True), self._entry(), cutoff, force=False
            )
        self.assertEqual("old", status)
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM article_archives").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
