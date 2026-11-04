"""backfill_raw_content.py 的离线单元测试（不联网）。"""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import backfill_raw_content as bf
from news_crawler import init_db


class _CountingCursor:
    """按需迭代的假游标，记录实际读取了多少行以及是否被关闭。"""

    def __init__(self, rows: list[tuple[str, str]], fail_after: int | None = None) -> None:
        self._rows = rows
        self._fail_after = fail_after
        self.consumed = 0
        self.closed = False

    def __iter__(self) -> Iterator[tuple[str, str]]:
        for index, row in enumerate(self._rows):
            if self._fail_after is not None and index >= self._fail_after:
                raise RuntimeError("模拟游标读取失败")
            self.consumed += 1
            yield row

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _CountingCursor) -> None:
        self._cursor = cursor
        self.sql = ""
        self.params: list[object] = []

    def execute(self, sql: str, params: list[object]) -> _CountingCursor:
        self.sql = sql
        self.params = params
        return self._cursor


class BackfillUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "news.db"
        init_db(str(self.db)).close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def insert(self, url: str, status: str = "ok", raw: str | None = None, source: str = "site") -> None:
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO articles (source, url, title, content, raw_content, status) VALUES (?, ?, ?, ?, ?, ?)",
            (source, url, "标题", "单行正文", raw, status),
        )
        conn.commit()
        conn.close()

    def raw_of(self, url: str) -> str | None:
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute("SELECT raw_content FROM articles WHERE url = ?", (url,)).fetchone()
        finally:
            conn.close()
        return None if row is None else row[0]

    def test_find_candidates_skip_and_limit(self) -> None:
        self.insert("https://example.com/ok.html", "ok")
        self.insert("https://example.com/und.html", "unknown_date")
        self.insert("https://example.com/old.html", "old")
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(len(bf.find_candidates(conn, "", 0)), 2)
            self.assertEqual(
                bf.find_candidates(conn, "", 0, {("site", "https://example.com/ok.html")}),
                [("site", "https://example.com/und.html")],
            )
            # limit 在 Python 侧截断，被 skip 的行不占名额
            self.assertEqual(
                bf.find_candidates(conn, "", 1, {("site", "https://example.com/ok.html")}),
                [("site", "https://example.com/und.html")],
            )
        finally:
            conn.close()

    def test_unknown_date_candidates_are_not_starved_by_limit(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.executemany(
                "INSERT INTO articles (source, url, publish_time, content, raw_content, status) "
                "VALUES ('site', ?, ?, '正文', NULL, ?)",
                [
                    ("https://example.com/new.html", "2026-10-03 10:00:00", "ok"),
                    ("https://example.com/old2.html", "2026-10-02 10:00:00", "ok"),
                    ("https://example.com/unknown.html", "", "unknown_date"),
                ],
            )
            conn.commit()
            # limit=1 时若按 publish_time DESC，空日期的 unknown 行永远取不到。
            self.assertEqual(
                bf.find_candidates(conn, "", 1),
                [("site", "https://example.com/unknown.html")],
            )
        finally:
            conn.close()

    def test_write_failed_list_cleans_all_columns(self) -> None:
        path = self.root / "failed.txt"
        bf.write_failed_list(path, [("si\tte", "https://x/\n", "第一行\t含tab\r\n第二行")])
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "# source\turl\terror")
        self.assertEqual(lines[1], "si te\thttps://x/\t第一行 含tab 第二行")

    def test_load_skip_list_accepts_bom_and_comments(self) -> None:
        path = self.root / "skip.txt"
        path.write_text("\ufeff# source\turl\terror\nsite\thttps://x/\tboom\n", encoding="utf-8")
        self.assertEqual(bf.load_skip_list(path), {("site", "https://x/")})

    def test_main_preserves_skip_entries_in_failed_list(self) -> None:
        url = "https://example.com/dead.html"
        self.insert(url)
        skip_file = self.root / "skip.txt"
        skip_file.write_text(f"site\t{url}\tdead\n", encoding="utf-8")
        self.assertEqual(bf.main(["--db", str(self.db), "--skip-file", str(skip_file), "--delay", "0"]), 0)
        text = (self.root / "backfill_failed.txt").read_text(encoding="utf-8")
        self.assertIn(url, text)
        self.assertIn("上次失败，本次跳过", text)
        self.assertIsNone(self.raw_of(url))

    def test_main_rejects_database_or_directory_as_failed_list(self) -> None:
        url = "https://127.0.0.1:1/unreachable.html"
        self.insert(url)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                bf.main(["--db", str(self.db), "--failed-list", str(self.db), "--delay", "0"]), 2
            )
        # 防呆在抓取之前：不应先打印候选并尝试网络
        self.assertNotIn("候选", out.getvalue())
        self.assertIsNone(self.raw_of(url))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bf.main(["--db", str(self.db), "--failed-list", str(self.root)]), 2)

    def test_main_rejects_hardlink_to_database(self) -> None:
        link_path = self.root / "db_link.txt"
        try:
            os.link(self.db, link_path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前文件系统不支持硬链接：{exc}")
        self.insert("https://127.0.0.1:1/unreachable.html")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bf.main(["--db", str(self.db), "--failed-list", str(link_path)]), 2)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM articles").fetchone()[0], 1)
        finally:
            conn.close()

    def test_find_candidates_stops_reading_once_limit_reached(self) -> None:
        skip = {("site", f"https://example.com/skip-{index}.html") for index in range(3)}
        rows = [("site", f"https://example.com/skip-{index}.html") for index in range(3)] + [
            ("site", f"https://example.com/keep-{index}.html") for index in range(100)
        ]
        cursor = _CountingCursor(rows)
        fake = cast(sqlite3.Connection, _FakeConnection(cursor))
        self.assertEqual(
            bf.find_candidates(fake, "", 2, skip),
            [("site", "https://example.com/keep-0.html"), ("site", "https://example.com/keep-1.html")],
        )
        # 3 条 skip + 2 条命中即停，不应读完 103 行
        self.assertEqual(cursor.consumed, 5)

    def test_find_candidates_pushes_limit_down_without_skip(self) -> None:
        rows = [("site", f"https://example.com/{index}.html") for index in range(10)]
        conn = _FakeConnection(_CountingCursor(rows))
        self.assertEqual(len(bf.find_candidates(cast(sqlite3.Connection, conn), "", 3)), 3)
        # skip 为空时 LIMIT 必须下推到 SQL，避免 SQLite 为 ORDER BY 全量排序
        self.assertIn("LIMIT ?", conn.sql)
        self.assertEqual(conn.params, ["ok", "unknown_date", 3])

    def test_find_candidates_limit_without_skip_real_sqlite(self) -> None:
        for index in range(5):
            self.insert(f"https://example.com/{index}.html")
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(len(bf.find_candidates(conn, "", 2)), 2)
            self.assertEqual(len(bf.find_candidates(conn, "", 0)), 5)
        finally:
            conn.close()

    def test_find_candidates_does_not_push_limit_when_skip_present(self) -> None:
        skip = {("site", "https://example.com/a.html")}
        conn = _FakeConnection(
            _CountingCursor([("site", "https://example.com/a.html"), ("site", "https://example.com/b.html")])
        )
        rows = bf.find_candidates(cast(sqlite3.Connection, conn), "", 1, skip)
        self.assertEqual(rows, [("site", "https://example.com/b.html")])
        self.assertNotIn("LIMIT", conn.sql)

    def test_find_candidates_closes_cursor_when_iteration_fails(self) -> None:
        cursor = _CountingCursor([("site", "https://example.com/a.html")], fail_after=0)
        fake = cast(sqlite3.Connection, _FakeConnection(cursor))
        with self.assertRaises(RuntimeError):
            bf.find_candidates(fake, "", 0)
        self.assertTrue(cursor.closed)

    def test_main_rejects_symlink_to_database(self) -> None:
        link_path = self.root / "db_symlink.txt"
        try:
            os.symlink(self.db, link_path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不支持符号链接：{exc}")
        self.insert("https://127.0.0.1:1/unreachable.html")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bf.main(["--db", str(self.db), "--failed-list", str(link_path)]), 2)

    def test_main_rejects_uncreated_wal_and_shm_sidecar_paths(self) -> None:
        self.insert("https://127.0.0.1:1/unreachable.html")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.db}{suffix}")
            # 防呆必须在 sidecar 尚不存在时也生效，否则 write_text 会创建伪 sidecar
            self.assertFalse(sidecar.exists())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bf.main(["--db", str(self.db), "--failed-list", str(sidecar)]), 2)
            self.assertFalse(sidecar.exists())

    def test_main_accepts_existing_failed_list_file(self) -> None:
        failed = self.root / "failed.txt"
        failed.write_text("# source\turl\terror\n", encoding="utf-8")
        self.insert("https://127.0.0.1:1/unreachable.html")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                bf.main(["--db", str(self.db), "--failed-list", str(failed), "--dry-run"]),
                0,
            )
        self.assertTrue(failed.is_file())


if __name__ == "__main__":
    unittest.main()
