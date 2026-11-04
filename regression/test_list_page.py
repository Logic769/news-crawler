"""列表页解析回归：锁定 ListPageExtractor 的条目定位与字段输出。

tests/ 下的 62 个样本都是文章详情页，列表页解析此前完全没有回归覆盖，
而它正是多站点抓取最容易因站点改版出问题的地方。这里用固定 fixture 锁定
「从一条标题向上找到同构兄弟节点」的核心行为。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from gne import ListPageExtractor

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "list_page.html"


class ListPageExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.extractor = ListPageExtractor()

    def test_extracts_all_items(self) -> None:
        items = self.extractor.extract(self.html, "示例新闻标题一")
        self.assertEqual(len(items), 10)
        self.assertEqual(
            items[0],
            {"title": "示例新闻标题一", "url": "/news/2026/11-01/1001.html"},
        )
        self.assertEqual(items[-1]["title"], "示例新闻标题十")

    def test_returns_empty_when_feature_missing(self) -> None:
        self.assertEqual(self.extractor.extract(self.html, "不存在的标题"), [])

    def test_requires_more_than_three_items(self) -> None:
        html = (
            '<html><body><ul class="list">'
            '<li><a class="title" href="/1.html">只有三条一</a></li>'
            '<li><a class="title" href="/2.html">只有三条二</a></li>'
            '<li><a class="title" href="/3.html">只有三条三</a></li>'
            "</ul></body></html>"
        )
        self.assertEqual(self.extractor.extract(html, "只有三条一"), [])


if __name__ == "__main__":
    unittest.main()
