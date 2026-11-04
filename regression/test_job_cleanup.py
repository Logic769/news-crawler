"""作业队列回归：过期作业清理失败不能阻断新作业提交。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import news_crawler
from news_api.jobs import JobManager
from news_api.settings import Settings


class JobCleanupTest(unittest.TestCase):
    def test_cleanup_failure_still_submits_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            conn = news_crawler.init_db(db_path)
            conn.close()
            settings = Settings(
                db_path=db_path,
                config_path=str(Path(tmp) / "sources.json"),
                api_key=None,
                job_workers=1,
                timezone="Asia/Shanghai",
            )
            manager = JobManager(settings)
            submitted: list[str] = []
            try:
                with mock.patch.object(
                    manager,
                    "_cleanup_finished",
                    side_effect=sqlite3.OperationalError("database is locked"),
                ), mock.patch.object(
                    manager,
                    "submit",
                    side_effect=lambda job_id: submitted.append(job_id),
                ):
                    job = manager.create("crawl", {"days": 1})
                self.assertEqual(submitted, [job["id"]])
                self.assertEqual(manager._last_cleanup, 0.0)
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
