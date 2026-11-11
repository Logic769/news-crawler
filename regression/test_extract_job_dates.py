"""extract 作业日期交叉校验回归：重抓时不能把已校准的列表日期改坏。"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import news_crawler
from news_api import tasks
from news_api.settings import Settings


class ExtractJobDatesTest(unittest.TestCase):
    def test_extract_job_falls_back_to_stored_list_date_when_skew_exceeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "article_title_xpath": "//h1",
                                "article_publish_time_xpath": "//p[@class='pub']",
                                "date_skew_days": 0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            list_time = f"{today.isoformat()} 00:00:00"
            detail_time = f"{(today - dt.timedelta(days=30)).isoformat()} 10:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/a",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": detail_time,
                "publish_time_source": "xpath",
                "title_source": "article_title_xpath",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/a", "source": "example"}, settings
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["published_at"], f"{today.isoformat()}T00:00:00+08:00")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, list_publish_time "
                    "FROM articles WHERE source = ? AND url = ?",
                    ("example", "https://example.com/a"),
                ).fetchone()
                assert row is not None
                self.assertEqual(row["publish_time"], list_time)
                self.assertEqual(row["publish_time_source"], "list")
                self.assertEqual(row["list_publish_time"], list_time)
            finally:
                conn.close()

    def test_extract_job_yearless_detail_keeps_stored_list_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "date_skew_days": 0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            reference = today.replace(month=1, day=1) - dt.timedelta(days=365)
            list_time = f"{reference.isoformat()} 00:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/yearless-with-list",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": "01月01日",
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/yearless-with-list", "source": "example"},
                    settings,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["published_at"], f"{reference.isoformat()}T00:00:00+08:00")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, list_publish_time "
                    "FROM articles WHERE source = ? AND url = ?",
                    ("example", "https://example.com/yearless-with-list"),
                ).fetchone()
                assert row is not None
                self.assertEqual(row["publish_time"], list_time)
                # 无年份详情按列表日期年份补齐后仍来自详情文本，来源保留 text。
                self.assertEqual(row["publish_time_source"], "text")
                self.assertEqual(row["list_publish_time"], list_time)
            finally:
                conn.close()

    def test_extract_job_uses_stored_list_date_when_detail_is_unparsable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "date_skew_days": 7,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            list_time = f"{today.isoformat()} 00:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/unparsable-detail",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": "不是日期",
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/unparsable-detail", "source": "example"},
                    settings,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["published_at"], f"{today.isoformat()}T00:00:00+08:00")

    def test_extract_job_aligns_yearless_before_skew_and_keeps_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "date_skew_days": 0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            future = today + dt.timedelta(days=20)
            reference = future.replace(year=future.year - 1)
            detail_raw = f"{future.month:02d}月{future.day:02d}日 09:30"
            list_time = f"{reference.isoformat()} 00:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/yearless-clock",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": detail_raw,
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/yearless-clock", "source": "example"},
                    settings,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(
                summary["published_at"], f"{reference.isoformat()}T09:30:00+08:00"
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, publish_time_raw "
                    "FROM articles WHERE source = ? AND url = ?",
                    ("example", "https://example.com/yearless-clock"),
                ).fetchone()
                assert row is not None
                self.assertEqual(row["publish_time"], f"{reference.isoformat()} 09:30:00")
                self.assertEqual(row["publish_time_source"], "text")
                self.assertEqual(row["publish_time_raw"], detail_raw)
            finally:
                conn.close()

    def test_extract_job_cross_year_yearless_detail_keeps_clock(self) -> None:
        # 库内列表日期在 1 月初、详情写去年 12 月：按列表年份会得到未来日期，
        # 必须回退上一年并保留详情时刻与来源。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "date_skew_days": 7,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            reference = today.replace(month=1, day=3)
            detail_date = reference.replace(year=reference.year - 1, month=12, day=28)
            detail_raw = f"{detail_date.month:02d}月{detail_date.day:02d}日 09:30"
            list_time = f"{reference.isoformat()} 00:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/cross-year",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": detail_raw,
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/cross-year", "source": "example"},
                    settings,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(
                summary["published_at"], f"{detail_date.isoformat()}T09:30:00+08:00"
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, publish_time_raw "
                    "FROM articles WHERE source = ? AND url = ?",
                    ("example", "https://example.com/cross-year"),
                ).fetchone()
                assert row is not None
                self.assertEqual(
                    row["publish_time"], f"{detail_date.isoformat()} 09:30:00"
                )
                self.assertEqual(row["publish_time_source"], "text")
                self.assertEqual(row["publish_time_raw"], detail_raw)
            finally:
                conn.close()

    def test_extract_job_cross_year_forward_yearless_detail_keeps_clock(self) -> None:
        # 库内列表日期在年末、详情写次年 1 月初：年份候选必须包含下一年。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_path = str(config_file)
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "date_skew_days": 30,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
            reference = today.replace(year=today.year - 2, month=12, day=28)
            detail_date = reference.replace(year=reference.year + 1, month=1, day=5)
            detail_raw = f"{detail_date.month:02d}月{detail_date.day:02d}日 09:30"
            list_time = f"{reference.isoformat()} 00:00:00"
            conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    conn,
                    "example",
                    "https://example.com/cross-year-forward",
                    publish_time=list_time,
                    publish_time_source="list",
                    list_publish_time=list_time,
                    title_source="gne_default",
                    status="ok",
                )
            finally:
                conn.close()

            settings = Settings(
                db_path=db_path,
                config_path=config_path,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": detail_raw,
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {
                        "url": "https://example.com/cross-year-forward",
                        "source": "example",
                    },
                    settings,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(
                summary["published_at"], f"{detail_date.isoformat()}T09:30:00+08:00"
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, publish_time_raw "
                    "FROM articles WHERE source = ? AND url = ?",
                    ("example", "https://example.com/cross-year-forward"),
                ).fetchone()
                assert row is not None
                self.assertEqual(
                    row["publish_time"], f"{detail_date.isoformat()} 09:30:00"
                )
                self.assertEqual(row["publish_time_source"], "text")
                self.assertEqual(row["publish_time_raw"], detail_raw)
            finally:
                conn.close()

    def test_extract_job_rejects_yearless_date_without_list_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            settings = Settings(
                db_path=db_path,
                config_path=str(root / "sources.json"),
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 80,
                "publish_time": "09月30日",
                "publish_time_source": "text",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ), mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/yearless"}, settings
                )
            self.assertEqual(summary["status"], "unknown_date")
            self.assertIsNone(summary["published_at"])
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT publish_time, publish_time_source FROM articles "
                    "WHERE url = ?",
                    ("https://example.com/yearless",),
                ).fetchone()
                assert row is not None
                self.assertEqual(row["publish_time"], "")
                self.assertEqual(row["publish_time_source"], "")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
