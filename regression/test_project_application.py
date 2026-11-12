"""项目申报列表特化回归：listing_type=project_application 的解析与入库。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import news_crawler

LIST_HTML = (
    "<html><body><table><tbody>\n"
    '<tr class="ant-table-row"><td><a class="detailLink" '
    'href="/project/detail/aaa/1">2026年度创新生态建设计划项目申报指南</a></td>\n'
    "<td>2026-11-30 16:30:00</td><td><div>申报中</div></td></tr>\n"
    '<tr class="ant-table-row"><td><a class="detailLink" '
    'href="/project/detail/bbb">2026年度科技成果孵化转化计划项目申报指南</a></td>\n'
    "<td>2026-10-01 16:30:00</td><td><div>已截止</div></td></tr>\n"
    "</tbody></table></body></html>"
)


def _source(**overrides: Any) -> dict:
    source: dict[str, Any] = {
        "name": "svc20",
        "timezone": "Asia/Shanghai",
        "listing_type": "project_application",
        "item_xpath": "//tr[contains(@class,'ant-table-row')]",
        "title_xpath": ".//a[contains(@class,'detailLink')]",
        "url_xpath": ".//a[contains(@class,'detailLink')]/@href",
        "deadline_xpath": "./td[2]",
        "deadline_format": "%Y-%m-%d %H:%M:%S",
        "project_status_xpath": "./td[3]",
        "content_required": False,
    }
    source.update(overrides)
    return source


def _new_conn() -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    db_path = str(Path(tmp.name) / "news.db")
    news_crawler.init_db(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn, tmp


class ProjectApplicationTest(unittest.TestCase):
    def test_load_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"name": "news_site", "list_url": "https://example.com"},
                            {"name": "proj_site", "listing_type": "project_application"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = news_crawler.load_config(str(path))
            news_site, proj_site = config["sources"]
            self.assertEqual(news_site["listing_type"], "news")
            self.assertTrue(news_site["content_required"])
            self.assertEqual(proj_site["listing_type"], "project_application")
            self.assertFalse(proj_site["content_required"])

    def test_load_config_unknown_listing_type_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps({"sources": [{"name": "x", "listing_type": "rss"}]}),
                encoding="utf-8",
            )
            config = news_crawler.load_config(str(path))
            self.assertEqual(config["sources"][0]["listing_type"], "news")

    def test_parse_list_items_extracts_deadline_and_status(self) -> None:
        entries = news_crawler.parse_list_items(
            LIST_HTML, _source(), "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first["project_status"], "申报中")
        self.assertEqual(first["deadline_text"], "2026-11-30 16:30:00")
        self.assertIsNotNone(first["deadline"])
        # 项目模式未配置 date_xpath 时不能把截止时间当发布时间
        self.assertIsNone(first["parsed"])
        self.assertEqual(
            first["url"], "https://svc20.stcsm.sh.gov.cn/project/detail/aaa/1"
        )
        self.assertEqual(entries[1]["project_status"], "已截止")

    def test_project_entry_saves_listing_fields_without_content(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度创新生态建设计划项目申报指南",
                "url": "https://example.com/detail/1",
                "date_text": "",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn, _source(), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT status, title, title_source, deadline, deadline_raw, "
                "project_status, publish_time, content FROM articles"
            ).fetchone()
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["title"], "2026年度创新生态建设计划项目申报指南")
            self.assertEqual(row["title_source"], "list_title")
            self.assertEqual(row["deadline"], "2026-11-30 16:30:00")
            self.assertEqual(row["deadline_raw"], "2026-11-30 16:30:00")
            self.assertEqual(row["project_status"], "申报中")
            self.assertEqual(row["publish_time"], "")
            self.assertEqual(row["content"], "")
        finally:
            conn.close()
            tmp.cleanup()

    def test_login_wall_short_content_is_not_saved(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/2",
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            wall = {
                "title": "上海市科技管理信息系统",
                "content": "还没有账号？ 立即注册",
                "publish_time": "",
                "images": [],
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=wall):
                status = news_crawler.process_entry(
                    conn, _source(), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT title, title_source, content FROM articles"
            ).fetchone()
            self.assertEqual(row["title"], "2026年度项目申报指南")
            self.assertEqual(row["title_source"], "list_title")
            self.assertEqual(row["content"], "")
        finally:
            conn.close()
            tmp.cleanup()

    def test_content_required_true_keeps_news_semantics(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/3",
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn, _source(content_required=True), entry, cutoff, force=False
                )
            self.assertEqual(status, "no_content")
        finally:
            conn.close()
            tmp.cleanup()

    def test_deadline_before_cutoff_marks_old(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2025年度项目申报指南",
                "url": "https://example.com/detail/4",
                "deadline_text": "2025-10-01 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2025-10-01 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "已截止",
            }
            status = news_crawler.process_entry(
                conn, _source(), entry, cutoff, force=False
            )
            self.assertEqual(status, "old")
            row = conn.execute("SELECT status, deadline FROM articles").fetchone()
            self.assertEqual(row["status"], "old")
            self.assertEqual(row["deadline"], "2025-10-01 16:30:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_deadline_does_not_fall_back_to_publish_time(self) -> None:
        """项目站点误配 date_xpath 时，deadline 不能取列表发布时间。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/no-deadline",
                "date_text": "2026-11-01",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-01", None, "Asia/Shanghai"
                ),
                "deadline_text": "",
                "deadline": None,
                "project_status": "申报中",
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn, _source(date_xpath="./td[1]"), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT deadline, deadline_raw, publish_time FROM articles"
            ).fetchone()
            self.assertEqual(row["deadline"], "")
            self.assertEqual(row["deadline_raw"], "")
            self.assertEqual(row["publish_time"], "")
        finally:
            conn.close()
            tmp.cleanup()

    def test_unparsable_deadline_keeps_raw_text(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "常年受理项目申报指南",
                "url": "https://example.com/detail/always",
                "date_text": "2026-11-01",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-01", None, "Asia/Shanghai"
                ),
                "deadline_text": "常年受理",
                "deadline": None,
                "project_status": "申报中",
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn, _source(), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT deadline, deadline_raw FROM articles"
            ).fetchone()
            self.assertEqual(row["deadline"], "")
            self.assertEqual(row["deadline_raw"], "常年受理")
        finally:
            conn.close()
            tmp.cleanup()

    def test_project_old_row_is_skipped_on_second_run(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2025年度项目申报指南",
                "url": "https://example.com/detail/old-skip",
                "deadline_text": "2025-10-01 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2025-10-01 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "已截止",
            }
            first = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(first, "old")
            updated_before = conn.execute(
                "SELECT updated_at FROM articles"
            ).fetchone()["updated_at"]
            second = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(second, "skip")
            updated_after = conn.execute(
                "SELECT updated_at FROM articles"
            ).fetchone()["updated_at"]
            self.assertEqual(updated_before, updated_after)
            widened = news_crawler.parse_datetime("2025-01-01", None, "Asia/Shanghai")
            assert widened is not None
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                third = news_crawler.process_entry(
                    conn, _source(), entry, widened, force=False
                )
            self.assertEqual(third, "ok")
            self.assertEqual(
                conn.execute("SELECT status FROM articles").fetchone()["status"], "ok"
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_force_old_entry_still_fetches_detail(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2025年度项目申报指南",
                "url": "https://example.com/detail/old-force",
                "deadline_text": "2025-10-01 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2025-10-01 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "已截止",
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ) as fetch, mock.patch.object(
                news_crawler, "extract_article", return_value=None
            ):
                status = news_crawler.process_entry(
                    conn, _source(content_required=True), entry, cutoff, force=True
                )
            self.assertEqual(status, "no_content")
            fetch.assert_called_once()
        finally:
            conn.close()
            tmp.cleanup()

    def test_project_entry_saves_long_content(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/with-content",
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            long_text = "申报条件与材料说明。" * 20
            detail = {
                "title": "站点名",
                "content": long_text,
                "publish_time": "",
                "images": [],
            }
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=detail):
                status = news_crawler.process_entry(
                    conn, _source(), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT title, title_source, content, content_length FROM articles"
            ).fetchone()
            self.assertEqual(row["title"], "2026年度项目申报指南")
            self.assertEqual(row["title_source"], "list_title")
            self.assertEqual(row["content"], long_text)
            self.assertEqual(row["content_length"], len(long_text))
        finally:
            conn.close()
            tmp.cleanup()

    def test_content_required_string_values_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"name": "a", "listing_type": "project_application",
                             "content_required": "false"},
                            {"name": "b", "listing_type": "project_application",
                             "content_required": 0},
                            {"name": "c", "listing_type": "project_application",
                             "content_required": "true"},
                            {"name": "d", "listing_type": "project_application",
                             "content_required": "yes"},
                            {"name": "e", "listing_type": "project_application",
                             "content_required": "abc"},
                            {"name": "f", "listing_type": "news",
                             "content_required": 2},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertLogs("news_crawler", level="WARNING") as logs:
                config = news_crawler.load_config(str(path))
            values = [s["content_required"] for s in config["sources"]]
            self.assertEqual(values, [False, False, True, True, False, True])
            messages = "\n".join(logs.output)
            self.assertIn("站点 'e' 的 content_required", messages)
            self.assertIn("站点 'f' 的 content_required", messages)

    def test_deadline_filter_and_order_in_query(self) -> None:
        from news_api import db as news_db

        conn, tmp = _new_conn()
        try:
            rows = [
                ("proj_early", "2026-03-01 16:30:00"),
                ("proj_late", "2026-12-31 16:30:00"),
                ("no_deadline", ""),
                ("null_deadline", None),
            ]
            for i, (url_id, deadline) in enumerate(rows):
                conn.execute(
                    "INSERT INTO articles (article_id, source, url, title, status, "
                    "deadline, content_length) VALUES (?, 'proj', ?, ?, 'ok', ?, 0)",
                    (f"id{i}", f"https://example.com/{url_id}", url_id, deadline),
                )
            conn.commit()
            data, meta = news_db.query_articles(
                str(Path(tmp.name) / "news.db"),
                status="ok",
                source="proj",
                published_from=None,
                published_to=None,
                deadline_from="2026-06-01 00:00:00",
                deadline_to=None,
                q=None,
                page=1,
                page_size=10,
                order="deadline_desc",
                include_content=False,
                timezones={},
                default_tz="Asia/Shanghai",
            )
            self.assertEqual(meta["total"], 1)
            self.assertEqual(data[0]["title"], "proj_late")
            data_all, _ = news_db.query_articles(
                str(Path(tmp.name) / "news.db"),
                status="ok",
                source="proj",
                published_from=None,
                published_to=None,
                deadline_from=None,
                deadline_to=None,
                q=None,
                page=1,
                page_size=10,
                order="deadline_asc",
                include_content=False,
                timezones={},
                default_tz="Asia/Shanghai",
            )
            titles = [r["title"] for r in data_all]
            self.assertEqual(titles[:2], ["proj_early", "proj_late"])
            self.assertEqual(set(titles[2:]), {"no_deadline", "null_deadline"})
            data_desc, _ = news_db.query_articles(
                str(Path(tmp.name) / "news.db"),
                status="ok",
                source="proj",
                published_from=None,
                published_to=None,
                deadline_from=None,
                deadline_to=None,
                q=None,
                page=1,
                page_size=10,
                order="deadline_desc",
                include_content=False,
                timezones={},
                default_tz="Asia/Shanghai",
            )
            desc_titles = [r["title"] for r in data_desc]
            self.assertEqual(desc_titles[:2], ["proj_late", "proj_early"])
            self.assertEqual(set(desc_titles[2:]), {"no_deadline", "null_deadline"})
            data_to, to_meta = news_db.query_articles(
                str(Path(tmp.name) / "news.db"),
                status="ok",
                source="proj",
                published_from=None,
                published_to=None,
                deadline_from=None,
                deadline_to="2026-12-31 23:59:59",
                q=None,
                page=1,
                page_size=10,
                order="deadline_asc",
                include_content=False,
                timezones={},
                default_tz="Asia/Shanghai",
            )
            self.assertEqual(to_meta["total"], 2)
            self.assertEqual([r["title"] for r in data_to], ["proj_early", "proj_late"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_migration_v8_adds_project_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "old.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE articles (
                    article_id TEXT, source TEXT NOT NULL, url TEXT NOT NULL,
                    list_title TEXT, title TEXT, publish_time TEXT,
                    publish_time_utc TEXT, publish_time_source TEXT,
                    publish_time_raw TEXT, list_publish_time TEXT,
                    title_source TEXT, timezone TEXT, author TEXT, content TEXT,
                    content_length INTEGER, raw_content TEXT, images TEXT,
                    status TEXT NOT NULL, error TEXT,
                    first_seen_at TEXT, updated_at TEXT,
                    PRIMARY KEY (source, url)
                )
                """
            )
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            conn.close()
            migrated = news_crawler.init_db(db_path)
            try:
                cols = {r[1] for r in migrated.execute("PRAGMA table_info(articles)")}
                self.assertTrue({"deadline", "deadline_raw", "project_status"} <= cols)
                self.assertEqual(
                    migrated.execute("PRAGMA user_version").fetchone()[0],
                    news_crawler.SCHEMA_VERSION,
                )
            finally:
                migrated.close()


if __name__ == "__main__":
    unittest.main()
