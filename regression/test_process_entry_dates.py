"""process_entry 日期交叉校验回归：详情日期异常时回退列表日期。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import news_crawler


def _result(
    publish_time: str,
    *,
    title_source: str = "article_title_xpath",
    publish_time_source: str = "xpath",
) -> dict:
    return {
        "title": "详情标题",
        "content": "正文内容。" * 80,
        "publish_time": publish_time,
        "title_source": title_source,
        "publish_time_source": publish_time_source,
        "author": "作者",
        "images": [],
    }


class ProcessEntryDatesTest(unittest.TestCase):
    def _run(
        self,
        detail_publish: str,
        list_publish: str | None,
        *,
        timezone: str = "Asia/Shanghai",
        date_skew_days: int | None = None,
        title_source: str = "article_title_xpath",
        publish_time_source: str = "xpath",
        expected_status: str = "ok",
        cutoff_text: str = "2026-01-01 00:00:00",
        seed_publish_raw: str = "",
    ) -> sqlite3.Row:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            init_conn = news_crawler.init_db(db_path)
            init_conn.close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                if seed_publish_raw:
                    news_crawler.save_article(
                        conn,
                        "source",
                        "https://example.com/a",
                        publish_time="2025-01-01 00:00:00",
                        publish_time_source="xpath",
                        publish_time_raw=seed_publish_raw,
                        status="ok",
                    )
                cutoff = news_crawler.parse_datetime(cutoff_text, None, timezone)
                assert cutoff is not None
                list_date = (
                    news_crawler.parse_datetime(list_publish, None, timezone)
                    if list_publish
                    else None
                )
                source: dict[str, Any] = {
                    "name": "source",
                    "timezone": timezone,
                    "article_title_xpath": "//h1",
                    "article_publish_time_xpath": "//p[@class='pub']",
                }
                if date_skew_days is not None:
                    source["date_skew_days"] = date_skew_days
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    return_value=("<html></html>", False),
                ), mock.patch.object(
                    news_crawler,
                    "extract_article",
                    return_value=_result(
                        detail_publish,
                        title_source=title_source,
                        publish_time_source=publish_time_source,
                    ),
                ) as extract_mock:
                    status = news_crawler.process_entry(
                        conn,
                        source,
                        {
                            "url": "https://example.com/a",
                            "title": "列表标题",
                            "parsed": list_date,
                        },
                        cutoff,
                        force=True,
                    )
                self.assertEqual(status, expected_status)
                extract_mock.assert_called_once()
                self.assertEqual(
                    extract_mock.call_args.kwargs["title_xpath"], "//h1"
                )
                self.assertEqual(
                    extract_mock.call_args.kwargs["publish_time_xpath"],
                    "//p[@class='pub']",
                )
                row = conn.execute(
                    "SELECT publish_time, publish_time_source, publish_time_raw, "
                    "list_publish_time, title_source FROM articles "
                    "WHERE source = ? AND url = ?",
                    ("source", "https://example.com/a"),
                ).fetchone()
                assert row is not None
                return row
            finally:
                conn.close()

    def test_future_detail_date_falls_back_to_list_date(self) -> None:
        # 用相对当前时刻生成未来日期，避免写死日期在明天变成“过去”而失效。
        today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        detail_date = today + timedelta(days=2)
        row = self._run(
            detail_date.strftime("%Y-%m-%d 16:00:00"),
            today.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(row["publish_time"], today.strftime("%Y-%m-%d 00:00:00"))
        self.assertEqual(row["publish_time_source"], "list")
        # 回退列表日期后，详情原文不再代表当前发布时间，应清空。
        self.assertEqual(row["publish_time_raw"], "")
        self.assertEqual(row["list_publish_time"], today.strftime("%Y-%m-%d 00:00:00"))
        self.assertEqual(row["title_source"], "article_title_xpath")

    def test_detail_date_far_from_list_date_falls_back_to_list_date(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        detail = today - timedelta(days=30)
        row = self._run(
            detail.strftime("%Y-%m-%d 00:00:00"),
            today.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(row["publish_time"], today.strftime("%Y-%m-%d 00:00:00"))
        self.assertEqual(row["publish_time_source"], "list")

    def test_detail_date_within_skew_keeps_detail_time(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        row = self._run(
            today.strftime("%Y-%m-%d 09:30:00"),
            (today - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(row["publish_time"], today.strftime("%Y-%m-%d 09:30:00"))
        self.assertEqual(row["publish_time_source"], "xpath")

    def test_skew_zero_keeps_detail_time_across_utc_date_boundary(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        detail_raw = (today - timedelta(days=1)).strftime("%Y-%m-%d") + "T23:30:00Z"
        row = self._run(
            detail_raw,
            today.strftime("%Y-%m-%d 00:00:00"),
            date_skew_days=0,
        )
        self.assertEqual(row["publish_time"], today.strftime("%Y-%m-%d 07:30:00"))
        self.assertEqual(row["publish_time_source"], "xpath")
        self.assertEqual(row["publish_time_raw"], detail_raw)
        self.assertEqual(row["list_publish_time"], today.strftime("%Y-%m-%d 00:00:00"))

    def test_yearless_detail_without_list_date_is_unknown(self) -> None:
        row = self._run(
            "09月30日",
            None,
            publish_time_source="text",
            expected_status="unknown_date",
        )
        self.assertEqual(row["publish_time"], "")
        self.assertEqual(row["publish_time_source"], "")
        self.assertEqual(row["publish_time_raw"], "09月30日")

    def test_yearless_detail_uses_list_date_year(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        # 用上一年的 1 月 1 日作为列表日期，并把 skew 放宽到足以走到
        # align_yearless_datetime 分支：若实现退化成“直接用当前年”，断言会失败。
        reference = today.replace(year=today.year - 1, month=1, day=1)
        row = self._run(
            "01月01日",
            reference.strftime("%Y-%m-%d 00:00:00"),
            date_skew_days=400,
            publish_time_source="text",
            cutoff_text=reference.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(row["publish_time"], reference.strftime("%Y-%m-%d 00:00:00"))
        self.assertEqual(row["publish_time_source"], "text")

    def test_yearless_detail_keeps_detail_clock_after_list_year_alignment(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        reference = today.replace(year=today.year - 1, month=9, day=30)
        row = self._run(
            "09月30日 09:30",
            reference.strftime("%Y-%m-%d 00:00:00"),
            publish_time_source="text",
            cutoff_text=reference.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(
            row["publish_time"], reference.strftime("%Y-%m-%d 09:30:00")
        )
        self.assertEqual(row["publish_time_source"], "text")

    def test_cross_year_yearless_detail_keeps_detail_clock(self) -> None:
        # 列表日期在 1 月初、详情写“12月28日”：按列表年份会得到未来日期，
        # 必须回退到上一年并保留详情时刻与来源，而不是整体换成列表日期。
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        reference = today.replace(month=1, day=3)
        detail = reference.replace(year=reference.year - 1, month=12, day=28)
        row = self._run(
            f"{detail.month:02d}月{detail.day:02d}日 09:30",
            reference.strftime("%Y-%m-%d 00:00:00"),
            publish_time_source="text",
            cutoff_text=detail.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(
            row["publish_time"], detail.strftime("%Y-%m-%d 09:30:00")
        )
        self.assertEqual(row["publish_time_source"], "text")

    def test_cross_year_forward_yearless_detail_keeps_detail_clock(self) -> None:
        # 列表日期在年末、详情写次年“01月05日”：年份候选必须包含下一年，
        # 否则偏差校验会把详情时刻和来源整体换成列表日期。
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        reference = today.replace(year=today.year - 2, month=12, day=28)
        detail = reference.replace(year=reference.year + 1, month=1, day=5)
        row = self._run(
            f"{detail.month:02d}月{detail.day:02d}日 09:30",
            reference.strftime("%Y-%m-%d 00:00:00"),
            date_skew_days=30,
            publish_time_source="text",
            cutoff_text=detail.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(
            row["publish_time"], detail.strftime("%Y-%m-%d 09:30:00")
        )
        self.assertEqual(row["publish_time_source"], "text")

    def test_future_yearless_detail_uses_list_year_and_keeps_clock(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        future = today + timedelta(days=20)
        if (future.month, future.day) == (2, 29):
            # 避开闰日：reference 是平年，replace(year=...) 会抛 ValueError。
            future += timedelta(days=1)
        reference = future.replace(year=future.year - 1)
        detail = f"{future.month:02d}月{future.day:02d}日 09:30"
        row = self._run(
            detail,
            reference.strftime("%Y-%m-%d 00:00:00"),
            date_skew_days=400,
            publish_time_source="text",
            cutoff_text=reference.strftime("%Y-%m-%d 00:00:00"),
        )
        self.assertEqual(
            row["publish_time"], reference.strftime("%Y-%m-%d 09:30:00")
        )
        self.assertEqual(row["publish_time_source"], "text")

    def test_relative_yesterday_without_list_date_is_preserved(self) -> None:
        row = self._run("昨天", None, publish_time_source="text")
        self.assertTrue(row["publish_time"])
        self.assertEqual(row["publish_time_source"], "text")

    def test_list_date_fallback_clears_stale_detail_raw(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        detail = today - timedelta(days=30)
        row = self._run(
            detail.strftime("%Y-%m-%d 10:00:00"),
            today.strftime("%Y-%m-%d 00:00:00"),
            seed_publish_raw="2020-01-01 00:00:00",
        )
        self.assertEqual(row["publish_time"], today.strftime("%Y-%m-%d 00:00:00"))
        self.assertEqual(row["publish_time_source"], "list")
        self.assertEqual(row["publish_time_raw"], "")

    def test_invalid_title_source_falls_back_to_gne_default(self) -> None:
        row = self._run(
            "2026-11-11 09:30:00",
            "2026-11-11 00:00:00",
            title_source="unknown_source",
        )
        self.assertEqual(row["title_source"], "gne_default")

    def test_missing_list_date_keeps_detail_source(self) -> None:
        row = self._run("2026-11-11 09:30:00", None)
        self.assertEqual(row["publish_time"], "2026-11-11 09:30:00")
        self.assertEqual(row["publish_time_source"], "xpath")
        self.assertEqual(row["list_publish_time"], "")

    def test_no_detail_and_no_list_date_is_unknown_with_empty_source(self) -> None:
        row = self._run(
            "",
            None,
            publish_time_source="",
            expected_status="unknown_date",
        )
        self.assertEqual(row["publish_time"], "")
        self.assertEqual(row["publish_time_source"], "")
        self.assertEqual(row["list_publish_time"], "")

    def test_date_skew_days_is_clamped(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"name": "low", "date_skew_days": -5},
                            {"name": "high", "date_skew_days": 1000},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = news_crawler.load_config(str(path))
            self.assertEqual(config["sources"][0]["date_skew_days"], 0)
            self.assertEqual(config["sources"][1]["date_skew_days"], 365)

    def test_unknown_date_does_not_overwrite_existing_time_source(self) -> None:
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
                    publish_time="2026-11-11 10:00:00",
                    publish_time_source="xpath",
                    title_source="article_title_xpath",
                    status="ok",
                )
                # unknown_date 本次没有可信时间，不能把旧来源改成 text。
                news_crawler.save_article(
                    conn,
                    "source",
                    "https://example.com/a",
                    publish_time="",
                    publish_time_source="text",
                    status="unknown_date",
                )
                row = conn.execute(
                    "SELECT publish_time, publish_time_source FROM articles "
                    "WHERE source = ? AND url = ?",
                    ("source", "https://example.com/a"),
                ).fetchone()
                assert row is not None
                self.assertEqual(row["publish_time"], "2026-11-11 10:00:00")
                self.assertEqual(row["publish_time_source"], "xpath")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
