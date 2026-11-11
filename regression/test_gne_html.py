"""HTML 预处理回归：<br> 断行保留，且不改写 JSON-LD 字符串。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from gne import GeneralNewsExtractor
from gne.extractors.author import AuthorExtractor
from gne.html import BR_SENTINEL, html2element

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
            "<html><head><title>测试标题</title></head><body><article>"
            "<div class='author'>作者甲<br>作者乙</div>"
            "<div class='content'><p>"
            + ("正文内容，" * 40)
            + "</p></div></article></body></html>"
        )
        # <title> 在不同 lxml 版本下的 br 解析不同，这里直接模拟提取器返回
        # 含哨兵的标题，专门验证 engine 的元数据归一化契约。
        with patch(
            "gne.engine.TitleExtractor.extract_with_source",
            return_value=(f"甲{BR_SENTINEL}乙", "gne_default"),
        ):
            result = GeneralNewsExtractor().extract(html)
        self.assertEqual(result["title"], "甲\n乙")
        self.assertEqual(result["author"], "作者甲\n作者乙")
        for field in ("title", "author", "publish_time", "content"):
            self.assertNotIn("\ue000", str(result[field]), field)

    def test_metadata_br_edges_are_stripped(self) -> None:
        html = (
            "<html><head><title>测试标题</title></head><body><article><p>"
            + ("正文内容，" * 40)
            + "</p></article></body></html>"
        )
        with patch(
            "gne.engine.TitleExtractor.extract_with_source",
            return_value=(f"{BR_SENTINEL}甲{BR_SENTINEL}", "gne_default"),
        ):
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
