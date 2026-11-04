"""process_entry 图片接线回归：抽取不到图片时不得清空库中已有图片。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import news_crawler
from url_extract import normalize_images_for_update


class ProcessEntryImagesTest(unittest.TestCase):
    def test_empty_extraction_keeps_existing_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            init_conn = news_crawler.init_db(db_path)
            init_conn.close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                news_crawler.save_article(
                    conn,
                    "source",
                    "https://example.com/a",
                    title="旧标题",
                    content="旧正文" * 60,
                    raw_content="旧正文",
                    images=["https://cdn.example.com/old.jpg"],
                    status="ok",
                )
                cutoff = news_crawler.parse_datetime(
                    "2026-01-01 00:00:00", None, "Asia/Shanghai"
                )
                assert cutoff is not None
                result = {
                    "title": "新标题",
                    "content": "新正文" * 60,
                    "publish_time": "2026-10-01 00:00:00",
                    "author": "",
                    "images": [],
                }
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    return_value=("<html></html>", False),
                ), mock.patch.object(
                    news_crawler, "extract_article", return_value=result
                ):
                    status = news_crawler.process_entry(
                        conn,
                        {"name": "source", "timezone": "Asia/Shanghai"},
                        {"url": "https://example.com/a", "title": "列表标题"},
                        cutoff,
                        force=True,
                    )
                self.assertEqual(status, "ok")
                row = conn.execute(
                    "SELECT images, content FROM articles "
                    "WHERE source = ? AND url = ?",
                    ("source", "https://example.com/a"),
                ).fetchone()
                assert row is not None
                self.assertEqual(
                    json.loads(row["images"]),
                    ["https://cdn.example.com/old.jpg"],
                )
                self.assertTrue(row["content"].startswith("新正文"))
            finally:
                conn.close()

    def test_normalize_images_for_update_treats_empty_as_none(self) -> None:
        self.assertIsNone(normalize_images_for_update([], "https://example.com/a"))
        self.assertIsNone(
            normalize_images_for_update(["javascript:alert(1)"], "https://example.com/a")
        )
        self.assertEqual(
            normalize_images_for_update(["//cdn.example.com/a.jpg"], "https://example.com/a"),
            ["https://cdn.example.com/a.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
