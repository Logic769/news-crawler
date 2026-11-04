"""HTML 预处理回归：<br> 断行保留，且不改写 JSON-LD 字符串。"""

from __future__ import annotations

import unittest

from gne import GeneralNewsExtractor
from gne.extractors.author import AuthorExtractor
from gne.html import html2element

ARTICLE_HTML = (
    "<html><head><title>测试标题</title></head><body><article>"
    "<h1>测试标题</h1><div class='content'>"
    "<p>第一行<br>第二行<br>第三行</p>"
    "<p>" + ("这是正文内容，" * 40) + "</p>"
    "<p>第三段更多正文内容。</p>"
    "</div></article></body></html>"
)


class GneHtmlTest(unittest.TestCase):
    def test_br_keeps_line_breaks_in_content(self) -> None:
        result = GeneralNewsExtractor().extract(ARTICLE_HTML)
        self.assertIn("第一行\n第二行\n第三行", result["content"])

    def test_br_sentinel_does_not_leak_into_metadata_fields(self) -> None:
        html = (
            "<html><head><title>甲<br>乙</title></head><body><article>"
            "<h1>甲<br>乙</h1><div class='author'>作者甲<br>作者乙</div>"
            "<div class='content'><p>"
            + ("正文内容，" * 40)
            + "</p></div></article></body></html>"
        )
        result = GeneralNewsExtractor().extract(html)
        self.assertIn("甲\n乙", result["title"])
        self.assertEqual(result["author"], "作者甲\n作者乙")
        for field in ("title", "author", "publish_time", "content"):
            self.assertNotIn("\ue000", str(result[field]), field)

    def test_metadata_br_edges_are_stripped(self) -> None:
        html = (
            "<html><head><title><br>甲<br></title></head><body><article><p>"
            + ("正文内容，" * 40)
            + "</p></article></body></html>"
        )
        result = GeneralNewsExtractor().extract(html)
        self.assertEqual(result["title"], "甲")

    def test_json_ld_with_br_is_not_rewritten(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">'
            '{"author": "张三", "description": "A<br>B"}'
            "</script></head></html>"
        )
        element = html2element(html)
        self.assertEqual(AuthorExtractor().extract_from_json_ld(element), "张三")
        script_text = element.xpath('//script[@type="application/ld+json"]/text()')[0]
        self.assertIn("<br>", script_text)


if __name__ == "__main__":
    unittest.main()
