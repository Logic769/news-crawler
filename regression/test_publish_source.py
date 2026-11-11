"""发布来源标记回归：内部抽取来源映射与视频页来源。"""
from __future__ import annotations

import unittest

import news_crawler
from url_extract import extract_video_meta


class PublishSourceTest(unittest.TestCase):
    def test_internal_sources_map_to_stored_values(self) -> None:
        cases = {
            "xpath": "xpath",
            "labeled_text": "labeled_text",
            "meta": "meta",
            "json_ld": "meta",
            "time_tag": "meta",
            "text": "text",
            "video_meta": "video_meta",
            "unexpected": "detail",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.publish_source_for(raw), expected)

    def test_video_meta_reports_video_source_when_date_found(self) -> None:
        html = (
            "<html><head>"
            "<meta property='og:title' content='视频标题'>"
            "<meta property='article:published_time' content='2026-11-11T10:00:00+08:00'>"
            "</head><body></body></html>"
        )
        result = extract_video_meta(html, "https://example.com/video/1")
        self.assertIsNotNone(result)
        self.assertEqual(result["publish_time_source"], "video_meta")
        self.assertEqual(result["publish_time"], "2026-11-11")


if __name__ == "__main__":
    unittest.main()
