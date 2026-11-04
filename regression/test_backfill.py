"""backfill_raw_content.py 的离线单元测试（不联网）。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import backfill_raw_content as bf
from news_crawler import init_db


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


if __name__ == "__main__":
    unittest.main()
