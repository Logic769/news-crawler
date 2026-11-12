"""项目申报列表特化回归：listing_type=project_application 的解析与入库。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

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


def _entry(url: str, deadline_text: str, project_status: str = "申报中") -> dict:
    return {
        "title": "2026年度项目申报指南",
        "url": url,
        "deadline_text": deadline_text,
        "deadline": news_crawler.parse_datetime(deadline_text, None, "Asia/Shanghai"),
        "project_status": project_status,
    }


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

    def test_refresh_ok_row_updates_deadline_without_fetching_detail(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-ok", "2026-11-30 16:30:00")
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                    "ok",
                )
            second = _entry("https://example.com/detail/refresh-ok", "2026-12-31 16:30:00")
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                side_effect=AssertionError("已入库项目行刷新时不应抓详情"),
            ):
                status = news_crawler.process_entry(
                    conn, _source(), second, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT status, deadline FROM articles").fetchone()
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["deadline"], "2026-12-31 16:30:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_ok_row_marks_old_when_deadline_passes(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-12-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-old", "2026-12-31 16:30:00")
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                    "ok",
                )
            second = _entry("https://example.com/detail/refresh-old", "2026-11-01 16:30:00")
            status = news_crawler.process_entry(conn, _source(), second, cutoff, force=False)
            self.assertEqual(status, "old")
            row = conn.execute("SELECT status, deadline FROM articles").fetchone()
            self.assertEqual(row["status"], "old")
            self.assertEqual(row["deadline"], "2026-11-01 16:30:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_old_row_becomes_ok_when_deadline_extended(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-extended", "2025-10-01 16:30:00")
            self.assertEqual(
                news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                "old",
            )
            second = _entry(
                "https://example.com/detail/refresh-extended", "2026-11-30 16:30:00"
            )
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                side_effect=AssertionError("续期只刷新列表字段，不应抓详情"),
            ):
                status = news_crawler.process_entry(
                    conn, _source(), second, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT status, deadline FROM articles").fetchone()
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["deadline"], "2026-11-30 16:30:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_unparsable_deadline_clears_stale_value(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-always", "2026-11-30 16:30:00")
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                    "ok",
                )
            entry = {
                "title": "常年受理项目申报指南",
                "url": "https://example.com/detail/refresh-always",
                "deadline_text": "常年受理",
                "deadline": None,
                "project_status": "常年受理",
            }
            status = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT deadline, deadline_raw FROM articles").fetchone()
            self.assertEqual(row["deadline"], "")
            self.assertEqual(row["deadline_raw"], "常年受理")
        finally:
            conn.close()
            tmp.cleanup()

    def test_deadline_yearless_future_keeps_current_year(self) -> None:
        now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
        parsed = news_crawler.parse_datetime(
            "12月31日",
            None,
            "Asia/Shanghai",
            allow_year_adjust=False,
            allow_future=True,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.year, now.year)
        default = news_crawler.parse_datetime("12月31日", None, "Asia/Shanghai")
        assert default is not None
        year_end = dt.datetime(now.year, 12, 31, tzinfo=now.tzinfo)
        if year_end > now + dt.timedelta(days=1):
            self.assertEqual(default.year, now.year - 1)
        else:
            self.assertEqual(default.year, now.year)

    def test_parse_list_items_yearless_deadline_rolls_to_next_year(self) -> None:
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/yearless">2027年度项目申报指南</a></td>'
            "<td>1月15日</td><td>申报中</td><td>2026-12-20</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual(deadline.strftime("%Y-%m-%d"), "2027-01-15")

    def test_yearless_deadline_format_uses_reference_year(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        parsed = news_crawler.parse_deadline_after(
            "01月15日", "%m月%d日", dt.datetime(2026, 12, 20, tzinfo=tz), "Asia/Shanghai"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2027-01-15")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/yearless-format">2027年度项目申报指南</a></td>'
            "<td>01月15日</td><td>申报中</td><td>2026-12-20</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(
            deadline_format="%m月%d日",
            date_xpath="./td[4]",
            date_format="%Y-%m-%d",
        )
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual(deadline.strftime("%Y-%m-%d"), "2027-01-15")

    def test_yearless_deadline_format_without_separator_uses_reference_year(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        parsed = news_crawler.parse_deadline_after(
            "1130", "%m%d", dt.datetime(2026, 11, 15, tzinfo=tz), "Asia/Shanghai"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2026-11-30")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/yearless-compact">2026年度项目申报指南</a></td>'
            "<td>1130</td><td>申报中</td><td>2026-11-15</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(
            deadline_format="%m%d",
            date_xpath="./td[4]",
            date_format="%Y-%m-%d",
        )
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual(deadline.strftime("%Y-%m-%d"), "2026-11-30")

    def test_deadline_feb_29_skips_non_leap_years(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        parsed = news_crawler.parse_deadline_after(
            "2月29日", None, dt.datetime(2026, 3, 1, tzinfo=tz), "Asia/Shanghai"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2028-02-29")

    def test_deadline_feb_29_candidate_extends_three_years(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        parsed = news_crawler.parse_deadline_after(
            "2月29日", None, dt.datetime(2025, 3, 1, tzinfo=tz), "Asia/Shanghai"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2028-02-29")

    def test_deadline_closed_status_constrains_year_to_past(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        reference = dt.datetime(2026, 11, 15, tzinfo=tz)
        future = news_crawler.parse_deadline_after(
            "10月01日", None, reference, "Asia/Shanghai"
        )
        self.assertIsNotNone(future)
        assert future is not None
        self.assertEqual(future.strftime("%Y-%m-%d"), "2027-10-01")
        past = news_crawler.parse_deadline_after(
            "10月01日", None, reference, "Asia/Shanghai", allow_past=True
        )
        self.assertIsNotNone(past)
        assert past is not None
        self.assertEqual(past.strftime("%Y-%m-%d"), "2026-10-01")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed">2026年度项目申报指南</a></td>'
            "<td>10月01日</td><td>已截止</td><td>2026-09-15</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        # 端到端窗口上界随抓取时刻推进，只断言不变量；精确值由固定窗口锁定。
        self.assertEqual((deadline.month, deadline.day), (10, 1))
        self.assertGreaterEqual(deadline.date(), dt.date(2026, 9, 15))
        self.assertLessEqual(deadline.date(), dt.datetime.now(tz).date())
        fixed = news_crawler.parse_deadline_after(
            "10月01日",
            None,
            dt.datetime(2026, 9, 15, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2026, 9, 15, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNotNone(fixed)
        assert fixed is not None
        self.assertEqual(fixed.strftime("%Y-%m-%d"), "2026-10-01")

    def test_project_status_closed_tolerates_inner_whitespace(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        self.assertTrue(news_crawler._project_status_closed("已 截止"))
        self.assertTrue(news_crawler._project_status_closed(" 已\u3000截止 "))
        self.assertFalse(news_crawler._project_status_closed("申报中"))
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed-space">2026年度项目申报指南</a></td>'
            "<td>10月01日</td><td>已 截止</td><td>2026-09-15</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        # 端到端窗口上界随抓取时刻推进，只断言不变量；精确值由固定窗口锁定。
        self.assertEqual((deadline.month, deadline.day), (10, 1))
        self.assertGreaterEqual(deadline.date(), dt.date(2026, 9, 15))
        self.assertLessEqual(deadline.date(), dt.datetime.now(tz).date())
        fixed = news_crawler.parse_deadline_after(
            "10月01日",
            None,
            dt.datetime(2026, 9, 15, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2026, 9, 15, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNotNone(fixed)
        assert fixed is not None
        self.assertEqual(fixed.strftime("%Y-%m-%d"), "2026-10-01")

    def test_deadline_closed_uses_year_after_publish(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed-cross-year">2026年度项目申报指南</a></td>'
            "<td>1月15日</td><td>已截止</td><td>2025-12-20</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        # 端到端受抓取时刻约束，只断言不变量，避免用例随时钟老化。
        self.assertEqual((deadline.month, deadline.day), (1, 15))
        self.assertGreaterEqual(deadline.date(), dt.date(2025, 12, 20))
        self.assertLessEqual(deadline.date(), dt.datetime.now(tz).date())
        # 精确年份用固定窗口的直调用例锁定（同时覆盖 deadline_format 分支）。
        fixed = news_crawler.parse_deadline_after(
            "1月15日",
            "%m月%d日",
            dt.datetime(2025, 12, 20, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2025, 12, 20, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNotNone(fixed)
        assert fixed is not None
        self.assertEqual(fixed.strftime("%Y-%m-%d"), "2026-01-15")

    def test_deadline_closed_on_publish_day(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed-same-day">2026年度项目申报指南</a></td>'
            "<td>11月5日</td><td>已截止</td><td>2026-11-05</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        deadline = entries[0]["deadline"]
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual((deadline.month, deadline.day), (11, 5))
        self.assertGreaterEqual(deadline.date(), dt.date(2026, 11, 5))
        fixed = news_crawler.parse_deadline_after(
            "11月5日",
            None,
            dt.datetime(2026, 11, 5, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2026, 11, 5, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNotNone(fixed)
        assert fixed is not None
        self.assertEqual(fixed.strftime("%Y-%m-%d"), "2026-11-05")

    def test_deadline_closed_without_plausible_year_keeps_raw(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed-implausible">2026年度项目申报指南</a></td>'
            "<td>10月01日</td><td>已截止</td><td>2026-11-01</td></tr>"
            "</tbody></table></body></html>"
        )
        source = _source(date_xpath="./td[4]", date_format="%Y-%m-%d")
        entries = news_crawler.parse_list_items(
            html, source, "https://svc20.stcsm.sh.gov.cn/project"
        )
        self.assertEqual(len(entries), 1)
        # 原文必须保留；可解析结果由固定窗口锁定（抓取时刻推进后属已知取舍）。
        self.assertEqual(entries[0]["deadline_text"], "10月01日")
        deadline = entries[0]["deadline"]
        self.assertTrue(
            deadline is None or deadline.date() <= dt.datetime.now(tz).date()
        )
        fixed = news_crawler.parse_deadline_after(
            "10月01日",
            None,
            dt.datetime(2026, 11, 1, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2026, 11, 1, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNone(fixed)

    def test_deadline_closed_feb_29_looks_back_four_years(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        parsed = news_crawler.parse_deadline_after(
            "2月29日",
            None,
            dt.datetime(2026, 11, 15, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2024-02-29")

    def test_refresh_updates_title_without_fetching_detail(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-title", "2026-11-30 16:30:00")
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                    "ok",
                )
            second = _entry(
                "https://example.com/detail/refresh-title", "2026-11-30 16:30:00"
            )
            second["title"] = "2026年度项目申报指南（修订版）"
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                side_effect=AssertionError("标题刷新不应抓详情"),
            ):
                status = news_crawler.process_entry(
                    conn, _source(), second, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT title, list_title FROM articles").fetchone()
            self.assertEqual(row["title"], "2026年度项目申报指南（修订版）")
            self.assertEqual(row["list_title"], "2026年度项目申报指南（修订版）")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_old_row_with_unparsable_deadline_becomes_ok(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-old-always", "2025-10-01 16:30:00")
            self.assertEqual(
                news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                "old",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/refresh-old-always",
                "deadline_text": "常年受理",
                "deadline": None,
                "project_status": "常年受理",
            }
            status = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT status, deadline, deadline_raw FROM articles").fetchone()
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["deadline"], "")
            self.assertEqual(row["deadline_raw"], "常年受理")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_project_row_clears_news_publish_time(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/mode-switch",
                list_title="旧新闻标题",
                title="旧新闻标题",
                publish_time="2026-11-01 10:00:00",
                publish_time_source="text",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = _entry("https://example.com/detail/mode-switch", "2026-11-30 16:30:00")
            status = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, publish_time_source, "
                "publish_time_raw, list_publish_time, title_source FROM articles"
            ).fetchone()
            self.assertEqual(
                tuple(row), ("", "", "", "", "", "list_title")
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_old_row_to_ok_required_content_fetches_detail(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            first = _entry("https://example.com/detail/refresh-required", "2025-10-01 16:30:00")
            self.assertEqual(
                news_crawler.process_entry(conn, _source(), first, cutoff, force=False),
                "old",
            )
            second = _entry(
                "https://example.com/detail/refresh-required", "2026-11-30 16:30:00"
            )
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ) as fetch_mock, mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn,
                    _source(content_required=True),
                    second,
                    cutoff,
                    force=False,
                )
            self.assertEqual(status, "no_content")
            fetch_mock.assert_called_once()
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_ok_row_retries_when_content_becomes_required(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = _entry(
                "https://example.com/detail/refresh-required-ok", "2026-11-30 16:30:00"
            )
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, _source(), entry, cutoff, force=False),
                    "ok",
                )
            row = conn.execute(
                "SELECT content_length, title_source FROM articles"
            ).fetchone()
            self.assertEqual(row["content_length"], 0)
            self.assertEqual(row["title_source"], "list_title")
            with mock.patch.object(
                news_crawler, "fetch_article_html", return_value=("<html></html>", False)
            ) as fetch_mock, mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(
                    conn,
                    _source(content_required=True),
                    entry,
                    cutoff,
                    force=False,
                )
            self.assertEqual(status, "no_content")
            fetch_mock.assert_called_once()
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_marks_legacy_row_with_list_title_source(self) -> None:
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/legacy-source",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="gne_default",
                deadline="2026-11-30 16:30:00",
                deadline_raw="2026-11-30 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = _entry(
                "https://example.com/detail/legacy-source", "2026-11-30 16:30:00"
            )
            status = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(status, "ok")
            row = conn.execute("SELECT title_source, status FROM articles").fetchone()
            self.assertEqual(row["title_source"], "list_title")
            self.assertEqual(row["status"], "ok")
        finally:
            conn.close()
            tmp.cleanup()

    def test_deadline_bound_is_site_wall_clock(self) -> None:
        from news_api.app import _wall_bound

        self.assertEqual(
            _wall_bound("2026-12-01T00:00:00+08:00", end_of_day=False),
            "2026-12-01 00:00:00",
        )
        self.assertEqual(
            _wall_bound("2026-12-01T00:00:00Z", end_of_day=False),
            "2026-12-01 00:00:00",
        )
        self.assertEqual(
            _wall_bound("2026-12-01", end_of_day=True), "2026-12-01 23:59:59"
        )

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
