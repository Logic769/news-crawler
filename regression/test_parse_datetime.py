"""日期解析回归：锁定 ISO8601 带显式时区的偏移量不被通用正则吞掉。"""

from __future__ import annotations

import unittest
from datetime import datetime

from news_crawler import format_publish_time, parse_datetime


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
