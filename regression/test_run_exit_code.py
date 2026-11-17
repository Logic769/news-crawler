"""抓取轮次退出码：全部失败返回非零，没有新文章不算失败。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import news_crawler


class RunExitCodeTests(unittest.TestCase):
    def test_all_errors_fail(self) -> None:
        self.assertEqual(news_crawler.run_exit_code({"error": 3}), 1)

    def test_no_new_articles_is_success(self) -> None:
        self.assertEqual(news_crawler.run_exit_code({"list_empty": 2}), 0)

    def test_mixed_errors_and_results_succeed(self) -> None:
        self.assertEqual(news_crawler.run_exit_code({"error": 1, "ok": 2}), 0)
        self.assertEqual(news_crawler.run_exit_code({"error": 1, "old": 2}), 0)

    def test_all_articles_unusable_is_failure(self) -> None:
        self.assertEqual(news_crawler.run_exit_code({"no_content": 3}), 1)
        self.assertEqual(news_crawler.run_exit_code({"unknown_date": 3}), 1)
        self.assertEqual(
            news_crawler.run_exit_code({"no_content": 1, "unknown_date": 1}), 1
        )

    def test_no_errors_is_success(self) -> None:
        self.assertEqual(news_crawler.run_exit_code({}), 0)


class RunSourceFetchFailureTests(unittest.TestCase):
    def test_fetch_failed_is_counted_as_error_not_list_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "news.db")
            conn = news_crawler.init_db(db_path)
            conn.close()
            collected = {
                "items": [],
                "pages": 0,
                "page_urls": [],
                "stop_reason": "fetch_failed",
            }
            with mock.patch.object(
                news_crawler, "collect_list_entries_detailed", return_value=collected
            ), mock.patch.object(news_crawler, "close_thread_browser"):
                _, counts = news_crawler.run_source(
                    {"name": "broken"},
                    {"timezone": "Asia/Shanghai"},
                    db_path,
                    dt.datetime.now(dt.timezone.utc),
                    False,
                    0,
                )
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["list_empty"], 0)
        self.assertEqual(news_crawler.run_exit_code(counts), 1)


if __name__ == "__main__":
    unittest.main()
