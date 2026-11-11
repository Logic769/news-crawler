"""日期解析回归：锁定 ISO8601 带显式时区的偏移量不被通用正则吞掉。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from news_crawler import (
    align_yearless_datetime,
    format_publish_time,
    has_explicit_year,
    is_yearless_month_day,
    parse_datetime,
)


class ParseDatetimeTest(unittest.TestCase):
    def test_iso8601_with_positive_offset(self) -> None:
        parsed = parse_datetime("2026-10-30T10:00:00+08:00")
        assert parsed is not None
        self.assertEqual(parsed.isoformat(), "2026-10-30T10:00:00+08:00")

    def test_iso8601_with_zulu(self) -> None:
        parsed = parse_datetime("2026-10-30T02:00:00Z")
        assert parsed is not None
        self.assertEqual(parsed.isoformat(), "2026-10-30T02:00:00+00:00")

    def test_iso8601_offset_without_colon_and_fraction(self) -> None:
        parsed = parse_datetime("2026-10-30 10:00:00.500+0800")
        assert parsed is not None
        self.assertEqual(parsed.isoformat(), "2026-10-30T10:00:00.500000+08:00")

    def test_naive_datetime_still_uses_site_timezone(self) -> None:
        parsed = parse_datetime("2026-10-30 10:00:00", None, "Asia/Shanghai")
        assert parsed is not None
        self.assertEqual(parsed.isoformat(), "2026-10-30T10:00:00+08:00")

    def test_invalid_offset_does_not_crash(self) -> None:
        # 非法偏移不再命中 ISO 分支，也不允许抛未捕获 ValueError；
        # 回退到站点时区的墙钟解析。
        for raw in ("2026-10-30T10:00:00+99:99", "2026-10-30T10:00:00-24:00"):
            parsed = parse_datetime(raw, None, "Asia/Shanghai")
            assert parsed is not None
            self.assertEqual(parsed.isoformat(), "2026-10-30T10:00:00+08:00")

    def test_future_detail_date_without_year_adjust_returns_none(self) -> None:
        future = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)
        raw = future.strftime("%Y-%m-%d %H:%M:%S")
        self.assertIsNone(
            parse_datetime(raw, None, "Asia/Shanghai", allow_year_adjust=False)
        )
        # 默认行为仍保留旧逻辑（未来日期视为去年），兼容历史调用方。
        self.assertIsNotNone(parse_datetime(raw, None, "Asia/Shanghai"))

    def test_future_detail_date_guarded_on_all_parse_paths(self) -> None:
        future = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)
        iso = future.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        day_first = f"{future.day} {future.year}.{future.month} 16:00"
        custom = future.strftime("%Y-%m-%d %H:%M")
        for raw, fmt in ((iso, None), (day_first, None), (custom, "%Y-%m-%d %H:%M")):
            with self.subTest(raw=raw):
                self.assertIsNone(
                    parse_datetime(
                        raw, fmt, "Asia/Shanghai", allow_year_adjust=False
                    )
                )

    def test_align_yearless_datetime_uses_reference_year(self) -> None:
        value = datetime(2026, 1, 1, 9, 30)
        reference = datetime(2025, 1, 1, 0, 0)
        aligned = align_yearless_datetime(value, reference, "Asia/Shanghai")
        assert aligned is not None
        self.assertEqual(aligned.isoformat(), "2025-01-01T09:30:00+08:00")

    def test_align_yearless_datetime_handles_cross_year_detail(self) -> None:
        value = datetime(2025, 12, 28, 9, 30)
        reference = datetime(2026, 1, 3, 0, 0)
        aligned = align_yearless_datetime(value, reference, "Asia/Shanghai")
        assert aligned is not None
        self.assertEqual(aligned.isoformat(), "2025-12-28T09:30:00+08:00")

    def test_align_yearless_datetime_returns_none_for_invalid_leap_day(self) -> None:
        # 基准前后年份（2025、2026）都不是闰年，2 月 29 日无法落成合法日期。
        value = datetime(2024, 2, 29, 9, 30)
        reference = datetime(2026, 1, 1, 0, 0)
        self.assertIsNone(align_yearless_datetime(value, reference, "Asia/Shanghai"))

    def test_align_yearless_datetime_accepts_leap_day_in_previous_year(self) -> None:
        # 基准 2025 年非闰年，但上一年 2024 是闰年，2 月 29 日应落到 2024-02-29。
        value = datetime(2024, 2, 29, 9, 30)
        reference = datetime(2025, 6, 1, 0, 0)
        aligned = align_yearless_datetime(value, reference, "Asia/Shanghai")
        assert aligned is not None
        self.assertEqual(aligned.isoformat(), "2024-02-29T09:30:00+08:00")

    def test_two_digit_year_is_recognized_and_parsed(self) -> None:
        self.assertTrue(has_explicit_year("18-03-08"))
        self.assertFalse(is_yearless_month_day("18-03-08"))
        parsed = parse_datetime("18-03-08", None, "Asia/Shanghai")
        assert parsed is not None
        self.assertEqual(parsed.strftime("%Y-%m-%d"), "2018-03-08")

    def test_relative_time_is_not_treated_as_yearless_month_day(self) -> None:
        for raw in ("昨天", "前天", "3小时前", "30分钟前", "2天前"):
            with self.subTest(raw=raw):
                self.assertFalse(is_yearless_month_day(raw))
        self.assertTrue(is_yearless_month_day("09月30日"))
        self.assertTrue(is_yearless_month_day("09-30"))

    def test_explicit_future_year_keeps_year_in_list_parse(self) -> None:
        future = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=40)
        raw = future.strftime("%Y-%m-%d 16:00:00")
        parsed = parse_datetime(raw, None, "Asia/Shanghai")
        assert parsed is not None
        self.assertEqual(parsed.date(), future.date())

    def test_reference_year_is_used_before_future_rejection(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        future = today + timedelta(days=20)
        if (future.month, future.day) == (2, 29):
            # 避开闰日：reference_year 是平年，2 月 29 日在目标年份不存在。
            future += timedelta(days=1)
        reference_year = future.year - 1
        raw = f"{future.month:02d}月{future.day:02d}日 09:30"
        parsed = parse_datetime(
            raw,
            None,
            "Asia/Shanghai",
            allow_year_adjust=False,
            reference_year=reference_year,
        )
        assert parsed is not None
        self.assertEqual(
            parsed.isoformat(),
            f"{reference_year:04d}-{future.month:02d}-{future.day:02d}T09:30:00+08:00",
        )

    def test_format_publish_time_converts_to_site_timezone(self) -> None:
        parsed = parse_datetime("2026-10-30T02:00:00Z")
        assert parsed is not None
        self.assertEqual(
            format_publish_time(parsed, "Asia/Shanghai"), "2026-10-30 10:00:00"
        )
        self.assertEqual(
            format_publish_time(datetime(2026, 10, 30, 10, 0), "Asia/Shanghai"),
            "2026-10-30 10:00:00",
        )


if __name__ == "__main__":
    unittest.main()
