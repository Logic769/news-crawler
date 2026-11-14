"""backfill_body_html.py 的离线单元测试（不联网）。"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

import backfill_body_html as bf


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE articles (source TEXT, url TEXT, title TEXT, content TEXT, "
        "raw_content TEXT, body_html TEXT, status TEXT, publish_time TEXT)"
    )
    return conn


class FindCandidatesTest(unittest.TestCase):
    def test_only_missing_html_and_backfillable_statuses(self) -> None:
        conn = _conn()
        conn.executemany(
            "INSERT INTO articles (source, url, body_html, status) VALUES (?, ?, ?, ?)",
            [
                ("site", "https://e.com/a", None, "ok"),
                ("site", "https://e.com/b", "", "unknown_date"),
                ("site", "https://e.com/c", "<p>已有</p>", "ok"),
                ("site", "https://e.com/d", None, "old"),
                ("site", "https://e.com/e", None, "error"),
            ],
        )
        rows = bf.find_candidates(conn, "", 0)
        self.assertEqual(rows, [("site", "https://e.com/a"), ("site", "https://e.com/b")])

    def test_source_filter_and_limit(self) -> None:
        conn = _conn()
        conn.executemany(
            "INSERT INTO articles (source, url, body_html, status) VALUES (?, ?, ?, ?)",
            [("a", f"https://e.com/{i}", None, "ok") for i in range(3)]
            + [("b", "https://e.com/b", None, "ok")],
        )
        self.assertEqual(len(bf.find_candidates(conn, "a", 2)), 2)
        self.assertEqual(bf.find_candidates(conn, "b", 0), [("b", "https://e.com/b")])


class BackfillTest(unittest.TestCase):
    def test_writes_html_without_touching_existing_content(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO articles (source, url, content, raw_content, body_html, status) "
            "VALUES ('site', 'https://e.com/a', '旧正文', '旧原始', NULL, 'ok')"
        )
        with mock.patch.object(bf, "extract_article", return_value={"body_html": "<p>新</p>"}):
            updated, failed, skipped, write_failed = bf.backfill(
                conn, [("site", "https://e.com/a")], delay=0, allow_browser=False, dry_run=False
            )
        self.assertEqual((updated, failed, skipped, write_failed), (1, 0, 0, 0))
        row = conn.execute(
            "SELECT content, raw_content, body_html FROM articles WHERE url='https://e.com/a'"
        ).fetchone()
        self.assertEqual(row, ("旧正文", "旧原始", "<p>新</p>"))

    def test_update_guard_keeps_newer_html(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO articles (source, url, body_html, status) "
            "VALUES ('site', 'https://e.com/a', '<p>已有</p>', 'ok')"
        )
        with mock.patch.object(bf, "extract_article", return_value={"body_html": "<p>迟到</p>"}):
            updated, failed, skipped, _ = bf.backfill(
                conn, [("site", "https://e.com/a")], delay=0, allow_browser=False, dry_run=False
            )
        self.assertEqual((updated, failed, skipped), (0, 0, 1))
        self.assertEqual(
            conn.execute("SELECT body_html FROM articles WHERE url='https://e.com/a'").fetchone()[0],
            "<p>已有</p>",
        )

    def test_empty_html_counts_as_failure(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO articles (source, url, body_html, status) VALUES ('site', 'https://e.com/a', NULL, 'ok')"
        )
        with mock.patch.object(bf, "extract_article", return_value={"body_html": "  "}):
            updated, failed, skipped, _ = bf.backfill(
                conn, [("site", "https://e.com/a")], delay=0, allow_browser=False, dry_run=False
            )
        self.assertEqual((updated, failed, skipped), (0, 1, 0))


if __name__ == "__main__":
    unittest.main()
