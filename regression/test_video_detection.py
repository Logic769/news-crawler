"""视频页判定回归：锁定 url_extract.is_video_page 的 URL 与 HTML 信号。

基线里的 is_video 只覆盖 HTML 侧（og:type / JSON-LD），本文件额外锁定
路径规则与「文章内嵌相关视频不算视频页」这一容易误判的边界。
"""

from __future__ import annotations

import unittest

from url_extract import is_video_page


class VideoPageDetectionTest(unittest.TestCase):
    def test_video_path_patterns(self) -> None:
        for url in (
            "https://example.com/shipin/2026/11-01/news1.html",
            "https://example.com/video/abc.html",
            "https://example.com/vod/abc.html",
            "https://example.com/videos/abc.html",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_video_page(url, ""))

    def test_og_type_video(self) -> None:
        html = '<html><head><meta property="og:type" content="video.other"></head></html>'
        self.assertTrue(is_video_page("https://example.com/a.html", html))

    def test_json_ld_video_object(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type": "VideoObject", "name": "x"}'
            "</script></head></html>"
        )
        self.assertTrue(is_video_page("https://example.com/a.html", html))

    def test_article_with_embedded_video_is_not_video_page(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@graph": [{"@type": "NewsArticle", "name": "x"}, '
            '{"@type": "VideoObject", "name": "y"}]}'
            "</script></head></html>"
        )
        self.assertFalse(is_video_page("https://example.com/a.html", html))

    def test_plain_article(self) -> None:
        html = "<html><body><p>普通文章</p></body></html>"
        self.assertFalse(is_video_page("https://example.com/a.html", html))


if __name__ == "__main__":
    unittest.main()
