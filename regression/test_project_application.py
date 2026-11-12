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
            # 配置了 date_xpath 时列表发布日期照常写入 publish_time；
            # 关键是 deadline 不取它，两者互不影响。
            self.assertEqual(row["publish_time"], "2026-11-01 00:00:00")
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

    def test_deadline_closed_candidates_cover_window_upper_bound(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        # 发布日期距抓取时刻超过三年时，候选年份必须覆盖窗口上界；
        # 只回看 reference.year + 3 会误取 2024-10-01。
        parsed = news_crawler.parse_deadline_after(
            "10月01日",
            None,
            dt.datetime(2021, 12, 20, tzinfo=tz),
            "Asia/Shanghai",
            allow_past=True,
            not_before=dt.datetime(2021, 12, 20, tzinfo=tz),
            not_after=dt.datetime(2026, 11, 12, tzinfo=tz),
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2026-10-01")
        now = dt.datetime.now(tz)
        list_date = (now - dt.timedelta(days=365 * 5)).date()
        html = (
            "<html><body><table><tbody>"
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/project/detail/closed-long-window">2022年度项目申报指南</a></td>'
            f"<td>10月01日</td><td>已截止</td><td>{list_date.isoformat()}</td></tr>"
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
        # 端到端窗口上界是抓取时刻，只断言不变量，精确值由上面的固定窗口锁定。
        # 列表日期取五年前：旧实现候选年只到 reference+3（约两年前），会被下面这条拦下。
        self.assertEqual((deadline.month, deadline.day), (10, 1))
        self.assertGreaterEqual(deadline.date(), list_date)
        self.assertLessEqual(deadline.date(), now.date())
        self.assertGreaterEqual(deadline.year, now.year - 1)

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
            # 源站没配 date_xpath：项目行的 publish_time 与 UTC 都清空
            # （UTC 写 NULL，与 save_article 的 NULL 语义一致）。
            self.assertEqual(
                tuple(row), ("", None, "", "", "", "list_title")
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_project_entry_saves_list_publish_date(self) -> None:
        """配置 date_xpath 时，项目行保留列表发布日期，且与 deadline 互不影响。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/publish-date",
                "date_text": "2026-11-05",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-05", None, "Asia/Shanghai"
                ),
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
                    conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, publish_time_source, "
                "publish_time_raw, list_publish_time, deadline FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["publish_time_source"], "list")
            self.assertEqual(row["publish_time_raw"], "2026-11-05")
            self.assertEqual(row["list_publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["deadline"], "2026-11-30 16:30:00")
            self.assertEqual(row["publish_time_utc"], "2026-11-04 16:00:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_project_entry_without_date_xpath_keeps_publish_time_empty(self) -> None:
        """未配置 date_xpath（只有截止时间列）时 publish_time 必须为空。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            entry = _entry("https://example.com/detail/no-date-xpath", "2026-11-30 16:30:00")
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                status = news_crawler.process_entry(conn, _source(), entry, cutoff, force=False)
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, publish_time_source, "
                "publish_time_raw, list_publish_time FROM articles"
            ).fetchone()
            self.assertEqual(
                tuple(row), ("", None, "", "", "")
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_updates_list_publish_date(self) -> None:
        """列表发布日期变化时，已入库项目行要跟着刷新，且不抓详情。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            source = _source(date_xpath="./td[4]")
            first = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/refresh-date",
                "date_text": "2026-11-01",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-01", None, "Asia/Shanghai"
                ),
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
                self.assertEqual(
                    news_crawler.process_entry(conn, source, first, cutoff, force=False),
                    "ok",
                )
            second = dict(first)
            second["date_text"] = "2026-11-03"
            second["parsed"] = news_crawler.parse_datetime(
                "2026-11-03", None, "Asia/Shanghai"
            )
            with mock.patch.object(
                news_crawler, "fetch_article_html"
            ) as fetch_mock, mock.patch.object(
                news_crawler, "extract_article", return_value=None
            ):
                status = news_crawler.process_entry(
                    conn, source, second, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            fetch_mock.assert_not_called()
            row = conn.execute(
                "SELECT publish_time, list_publish_time FROM articles"
            ).fetchone()
            self.assertEqual(
                tuple(row), ("2026-11-03 00:00:00", "2026-11-03 00:00:00")
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_keeps_old_publish_date_when_list_date_unparsable(self) -> None:
        """列表发布日期本次解析失败时保留旧值，不因页面抖动丢日期。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            source = _source(date_xpath="./td[4]")
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/date-flicker",
                "date_text": "2026-11-01",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-01", None, "Asia/Shanghai"
                ),
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
                self.assertEqual(
                    news_crawler.process_entry(conn, source, entry, cutoff, force=False),
                    "ok",
                )
            degraded = dict(entry)
            degraded["date_text"] = "待定"
            degraded["parsed"] = None
            status = news_crawler.process_entry(
                conn, source, degraded, cutoff, force=False
            )
            self.assertEqual(status, "skip")
            row = conn.execute("SELECT publish_time FROM articles").fetchone()
            self.assertEqual(row["publish_time"], "2026-11-01 00:00:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_warns_when_keeping_non_list_publish_source(self) -> None:
        """列表日期本次不可解析而沿用新闻遗留日期时，要留下 WARNING 便于排查。

        只有截止时间延期（deadline 变化）触发 UPDATE，标题与 title_source 全部
        相同，确保断言不依赖「标题变化」这类无关条件。
        """
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/legacy-kept",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 10:00:00",
                publish_time_source="xpath",
                publish_time_raw="2026年11月5日 10:00",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/legacy-kept",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            with self.assertLogs("news_crawler", level="WARNING") as logs:
                status = news_crawler.process_entry(
                    conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
                )
            # 截止时间延期 → 走 UPDATE 分支；关键是不清空、不改来源。
            self.assertEqual(status, "ok")
            self.assertTrue(any("沿用旧发布时间" in line for line in logs.output))
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, publish_time_source "
                "FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 10:00:00")
            # 沿用非 list 来源的旧墙钟时同样按行内时区换算：上海 10:00 → UTC 02:00
            self.assertEqual(row["publish_time_utc"], "2026-11-05 02:00:00")
            self.assertEqual(row["publish_time_source"], "xpath")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_clears_list_publish_time_when_no_local_publish_time(self) -> None:
        """没有任何可沿用的本地墙钟时，不能留下「有列表日期却没有发布时间」的态。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/empty-publish",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time_source="xpath",
                publish_time_raw="2026年11月5日",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/empty-publish",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            status = news_crawler.process_entry(
                conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
            )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, publish_time_source, "
                "publish_time_raw, list_publish_time FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "")
            self.assertIsNone(row["publish_time_utc"])
            self.assertEqual(row["publish_time_source"], "")
            self.assertEqual(row["publish_time_raw"], "")
            self.assertEqual(row["list_publish_time"], "")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_does_not_warn_on_unchanged_rows(self) -> None:
        """未变化的行不写库，也不应每轮重复打「沿用旧发布时间」WARNING。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/quiet-skip",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 10:00:00",
                publish_time_source="xpath",
                publish_time_raw="2026年11月5日 10:00",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-30 16:30:00",
                deadline_raw="2026-11-30 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/quiet-skip",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            with self.assertNoLogs("news_crawler", level="WARNING"):
                status = news_crawler.process_entry(
                    conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
                )
            self.assertEqual(status, "skip")
            row = conn.execute("SELECT publish_time FROM articles").fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 10:00:00")
        finally:
            conn.close()
            tmp.cleanup()
    def test_no_content_retry_keeps_list_publish_date(self) -> None:
        """正文重试期间不能把已保留的列表发布日期抹掉。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            source = _source(date_xpath="./td[4]", content_required=True)
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/keep-date",
                "date_text": "2026-11-05",
                "parsed": news_crawler.parse_datetime(
                    "2026-11-05", None, "Asia/Shanghai"
                ),
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            # 首轮：列表日期解析成功，但详情抽不到正文 → no_content，日期已入库。
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(conn, source, entry, cutoff, force=False),
                    "no_content",
                )
            row = conn.execute(
                "SELECT publish_time, publish_time_source, list_publish_time FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["publish_time_source"], "list")

            # 第二轮：列表日期本次不可解析（refresh 沿用旧值并要求重试正文），
            # 正文仍然抽不到；旧日期不能被这次失败清空。
            degraded = dict(entry)
            degraded["date_text"] = "待定"
            degraded["parsed"] = None
            with mock.patch.object(
                news_crawler,
                "fetch_article_html",
                return_value=("<html></html>", False),
            ), mock.patch.object(news_crawler, "extract_article", return_value=None):
                self.assertEqual(
                    news_crawler.process_entry(
                        conn, source, degraded, cutoff, force=False
                    ),
                    "no_content",
                )
            row = conn.execute(
                "SELECT publish_time, publish_time_source, list_publish_time, deadline "
                "FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["publish_time_source"], "list")
            self.assertEqual(row["list_publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["deadline"], "2026-11-30 16:30:00")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_fixes_legacy_publish_source_to_list(self) -> None:
        """历史新闻行切项目后，即使日期值不变也要把来源改写成 list。

        除 publish_time_source / publish_time_raw 之外，其余字段全部与本次列表
        数据一致（含 title 与 title_source），因此只有「五元组比较」能触发
        UPDATE；退回三元组比较时本用例必须变红，否则是假覆盖。
        """
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            # 历史新闻模式残留：日期值与本次列表日期相同，但来源/原文是详情抽取。
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/legacy-source",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 00:00:00",
                publish_time_source="xpath",
                publish_time_raw="2026年11月5日",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-30 16:30:00",
                deadline_raw="2026-11-30 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/legacy-source",
                "date_text": "2026-11-05",
                "parsed": news_crawler.parse_datetime("2026-11-05", None, "Asia/Shanghai"),
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
                    conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
                )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_source, publish_time_raw FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time_source"], "list")
            self.assertEqual(row["publish_time_raw"], "2026-11-05")
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_does_not_treat_utc_clock_as_local_publish_time(self) -> None:
        """publish_time 为空而 publish_time_utc 有值时，不能把 UTC 墙钟当本地时间。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/utc-only",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time_source="xpath",
                publish_time_raw="2026年11月5日",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-30 16:30:00",
                deadline_raw="2026-11-30 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            # 只有 UTC 墙钟、没有本地墙钟：模拟历史脏数据。
            conn.execute(
                "UPDATE articles SET publish_time_utc = '2026-11-04 16:00:00' "
                "WHERE source = 'svc20'"
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/utc-only",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "Asia/Shanghai"
                ),
                "project_status": "申报中",
            }
            status = news_crawler.process_entry(
                conn, _source(date_xpath="./td[4]"), entry, cutoff, force=False
            )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc FROM articles"
            ).fetchone()
            # 不能把 UTC 墙钟 2026-11-04 16:00 当成本地墙钟写回 publish_time。
            self.assertEqual(row["publish_time"], "")
            self.assertIsNone(row["publish_time_utc"])
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

    def test_parse_list_items_project_does_not_fall_back_to_url_date(self) -> None:
        """项目行不认 URL 里的日期：date_xpath 不可解析时保持 parsed=None。"""
        html = (
            '<html><body><table><tbody>'
            '<tr class="ant-table-row"><td><a class="detailLink" '
            'href="/2025/11/01/detail.html">2026年度项目申报指南</a></td>'
            "<td>2026-11-30 16:30:00</td><td><div>申报中</div></td>"
            "<td>待定</td></tr>"
            "</tbody></table></body></html>"
        )
        entries = news_crawler.parse_list_items(
            html,
            _source(date_xpath="./td[4]", date_format="%Y-%m-%d"),
            "https://example.com/project",
        )
        self.assertEqual(len(entries), 1)
        # 待定 + URL 里的 /2025/11/01/ 都不能变成列表发布日期。
        self.assertIsNone(entries[0]["parsed"])
        self.assertEqual(entries[0]["date_text"], "待定")

        # 新闻模式仍保留 URL 日期回退（同一份 HTML，只换 listing_type）。
        news_entries = news_crawler.parse_list_items(
            html,
            _source(listing_type="news", date_xpath="./td[4]", date_format="%Y-%m-%d"),
            "https://example.com/project",
        )
        self.assertIsNotNone(news_entries[0]["parsed"])
        self.assertEqual(
            news_crawler.format_publish_time(
                news_entries[0]["parsed"], "Asia/Shanghai"
            ),
            "2025-11-01 00:00:00",
        )

    def test_refresh_converts_kept_publish_time_with_row_timezone(self) -> None:
        """沿用旧墙钟时按行内 timezone 换算 UTC，站点配置改过也不重算。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/tz-kept",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 00:00:00",
                publish_time_source="list",
                publish_time_raw="2026-11-05",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/tz-kept",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "UTC"
                ),
                "project_status": "申报中",
            }
            # 站点 timezone 配置被改成 UTC：旧墙钟 2026-11-05 00:00 是上海时间。
            status = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, timezone FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["publish_time_utc"], "2026-11-04 16:00:00")
            self.assertEqual(row["timezone"], "Asia/Shanghai")
            # 幂等：同一份列表数据再跑一轮不应再写库（updated_at 不变）。
            updated_at = conn.execute(
                "SELECT updated_at FROM articles"
            ).fetchone()["updated_at"]
            again = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(again, "skip")
            self.assertEqual(
                conn.execute("SELECT updated_at FROM articles").fetchone()["updated_at"],
                updated_at,
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_syncs_timezone_when_accepting_list_date(self) -> None:
        """接受本次列表日期时按站点当前时区换算，并同步写入行内 timezone。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/tz-new",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-01 00:00:00",
                publish_time_source="list",
                publish_time_raw="2026-11-01",
                list_publish_time="2026-11-01 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/tz-new",
                "date_text": "2026-11-05",
                "parsed": news_crawler.parse_datetime("2026-11-05", None, "UTC"),
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "UTC"
                ),
                "project_status": "申报中",
            }
            status = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, timezone FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            self.assertEqual(row["publish_time_utc"], "2026-11-05 00:00:00")
            self.assertEqual(row["timezone"], "UTC")
            # 补写 timezone 只发生一次：第二轮同样的列表数据必须 skip。
            updated_at = conn.execute(
                "SELECT updated_at FROM articles"
            ).fetchone()["updated_at"]
            again = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(again, "skip")
            self.assertEqual(
                conn.execute("SELECT updated_at FROM articles").fetchone()["updated_at"],
                updated_at,
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_keeps_row_timezone_unknown_when_row_lacks_timezone(self) -> None:
        """行内 timezone 缺失时不把站点配置写回，避免把「未知」固化成确定时区。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/tz-unknown",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 00:00:00",
                publish_time_source="list",
                publish_time_raw="2026-11-05",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            # v6 之前入库的历史行迁移后 timezone 为 NULL，publish_time_utc 已回填。
            conn.execute("UPDATE articles SET timezone = NULL")
            conn.commit()
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/tz-unknown",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "UTC"
                ),
                "project_status": "申报中",
            }
            status = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(status, "ok")
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, timezone FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            # 已有的 UTC 换算是唯一可信口径，不能按站点新时区重算。
            self.assertEqual(row["publish_time_utc"], "2026-11-04 16:00:00")
            self.assertIsNone(row["timezone"])
            # 不写回时区后仍然幂等：第二轮不再 UPDATE。
            updated_at = conn.execute(
                "SELECT updated_at FROM articles"
            ).fetchone()["updated_at"]
            again = news_crawler.process_entry(
                conn,
                _source(date_xpath="./td[4]", timezone="UTC"),
                entry,
                cutoff,
                force=False,
            )
            self.assertEqual(again, "skip")
            self.assertEqual(
                conn.execute("SELECT updated_at FROM articles").fetchone()["updated_at"],
                updated_at,
            )
        finally:
            conn.close()
            tmp.cleanup()

    def test_refresh_guesses_utc_without_writing_timezone(self) -> None:
        """行内既无时区也无 UTC 时按站点配置换算，但不把猜测写回 timezone。"""
        conn, tmp = _new_conn()
        try:
            cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
            assert cutoff is not None
            news_crawler.save_article(
                conn,
                "svc20",
                "https://example.com/detail/tz-guess",
                list_title="2026年度项目申报指南",
                title="2026年度项目申报指南",
                title_source="list_title",
                publish_time="2026-11-05 00:00:00",
                publish_time_source="list",
                publish_time_raw="2026-11-05",
                list_publish_time="2026-11-05 00:00:00",
                deadline="2026-11-20 16:30:00",
                deadline_raw="2026-11-20 16:30:00",
                project_status="申报中",
                timezone="Asia/Shanghai",
                status="ok",
            )
            conn.execute(
                "UPDATE articles SET timezone = NULL, publish_time_utc = NULL"
            )
            conn.commit()
            entry = {
                "title": "2026年度项目申报指南",
                "url": "https://example.com/detail/tz-guess",
                "date_text": "待定",
                "parsed": None,
                "deadline_text": "2026-11-30 16:30:00",
                "deadline": news_crawler.parse_datetime(
                    "2026-11-30 16:30:00", None, "UTC"
                ),
                "project_status": "申报中",
            }
            with self.assertLogs("news_crawler", level="WARNING") as logs:
                status = news_crawler.process_entry(
                    conn,
                    _source(date_xpath="./td[4]", timezone="UTC"),
                    entry,
                    cutoff,
                    force=False,
                )
            self.assertEqual(status, "ok")
            self.assertTrue(any("缺少行内时区" in line for line in logs.output))
            row = conn.execute(
                "SELECT publish_time, publish_time_utc, timezone FROM articles"
            ).fetchone()
            self.assertEqual(row["publish_time"], "2026-11-05 00:00:00")
            # 没有可信口径，只能按站点当前时区猜一次。
            self.assertEqual(row["publish_time_utc"], "2026-11-05 00:00:00")
            self.assertIsNone(row["timezone"])
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
