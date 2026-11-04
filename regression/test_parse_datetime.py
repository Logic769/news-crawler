"""日期解析回归：锁定 ISO8601 带显式时区的偏移量不被通用正则吞掉。"""

from __future__ import annotations

import unittest

from news_crawler import parse_datetime


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


if __name__ == "__main__":
    unittest.main()
