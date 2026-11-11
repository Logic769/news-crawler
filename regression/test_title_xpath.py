"""标题 XPath 抽取回归：覆盖元素节点、文本节点与 XPath 1.0 标量结果。

背景（PR #28 CodeRabbit 评审）：
- 元素节点（//h1）应取全部后代文本，不能只取第一个文本节点；
- /text() 只返回直接文本节点，标题含 <em> 等子标签时会截断（文档已改为推荐元素节点）；
- normalize-space()/string() 等返回字符串标量，必须整体使用，不能 title_list[0]；
- count()/boolean() 返回数字/布尔标量，没有文本语义，应返回空串而不是抛 TypeError。
"""

from __future__ import annotations

import unittest

from lxml.html import fromstring

from gne import GeneralNewsExtractor
from gne.extractors.title import TitleExtractor
from news_crawler import _validate_title_xpath, _validate_xpath_field

HTML = "<html><body><h1 class='t'>重要<em>通知</em>发布</h1><h2>副标题</h2></body></html>"


class TitleXPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.element = fromstring(HTML)
        self.extractor = TitleExtractor()

    def test_element_node_returns_all_descendant_text(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "//h1"), "重要通知发布")

    def test_text_xpath_only_returns_first_text_node(self) -> None:
        # 记录 /text() 的截断语义：文档因此推荐元素节点而不是 /text()。
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "//h1/text()"), "重要")

    def test_string_scalar_uses_whole_string(self) -> None:
        self.assertEqual(
            self.extractor.extract_by_xpath(self.element, "normalize-space(//h1)"),
            "重要通知发布",
        )

    def test_numeric_scalar_returns_empty(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "count(//h1)"), "")

    def test_boolean_scalar_returns_empty(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "boolean(//h1)"), "")

    def test_string_length_scalar_returns_empty(self) -> None:
        self.assertEqual(
            self.extractor.extract_by_xpath(self.element, "string-length(//h1)"), ""
        )

    def test_number_scalar_returns_empty(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "number(//h1)"), "")

    def test_invalid_xpath_returns_empty(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, "//h1["), "")

    def test_empty_xpath_returns_empty(self) -> None:
        self.assertEqual(self.extractor.extract_by_xpath(self.element, ""), "")

    def test_engine_exposes_extraction_sources(self) -> None:
        html = (
            "<html><body><h1>重要通知</h1>"
            "<p class='pub'>发布时间：2026-11-11</p>"
            "<article><p>" + "正文内容。" * 50 + "</p></article></body></html>"
        )
        result = GeneralNewsExtractor().extract(
            html, title_xpath="//h1", publish_time_xpath="//p[@class='pub']"
        )
        self.assertEqual(result["title_source"], "article_title_xpath")
        self.assertEqual(result["publish_time_source"], "xpath")

    def test_gne_extract_with_element_title_xpath(self) -> None:
        html = (
            "<html><body><h1>重要<em>通知</em>发布</h1>"
            "<article><p>" + "正文内容。" * 50 + "</p></article></body></html>"
        )
        result = GeneralNewsExtractor().extract(html, title_xpath="//h1")
        self.assertEqual(result["title"], "重要通知发布")


class TitleXPathConfigTest(unittest.TestCase):
    def test_element_xpath_is_kept(self) -> None:
        self.assertEqual(_validate_title_xpath("demo", "//h1"), "//h1")

    def test_string_prefix_is_ignored(self) -> None:
        self.assertEqual(_validate_title_xpath("demo", "string(//h1)"), "")

    def test_invalid_xpath_is_ignored(self) -> None:
        self.assertEqual(_validate_title_xpath("demo", "//h1["), "")

    def test_publish_time_scalar_xpaths_are_ignored(self) -> None:
        for xpath in ("count(//p)", "string-length(//p)", "number(//p)", "boolean(//p)"):
            with self.subTest(xpath=xpath):
                self.assertEqual(
                    _validate_xpath_field("demo", xpath, "article_publish_time_xpath"), ""
                )

    def test_publish_time_element_xpath_is_kept(self) -> None:
        self.assertEqual(
            _validate_xpath_field(
                "demo", "//p[@class='pub']", "article_publish_time_xpath"
            ),
            "//p[@class='pub']",
        )


if __name__ == "__main__":
    unittest.main()
