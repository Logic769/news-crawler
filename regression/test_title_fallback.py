"""标题兜底回归：<title> 只是站点名时改用页面唯一 h1。

背景（most_kjjh 线上事故）：18/18 篇 ok 行的 title 全都成了站点名
「国家科技管理信息系统公共服务平台」，真标题只在正文 h1 里。原回退链
（htag_and_title -> og:title -> json-ld -> <title> -> h1）此时取到的是 <title>，
而 htag_and_title 因为最长公共子串不足 5 字被丢弃。

修复只加一条窄路径：前三级都没命中、且 <title> 「像站点名」、且页面
恰好只有一个「不像站点名」的 h1 时才覆盖。下面 19 个用例逐个守住这些守卫，
不能为了多修而放宽——tests/shanxi 的单 h1 含「网站」、tests/mingridapan 的
单 h1 是 62 字含「 - 」的 SEO 串，两个上游样本都因此被挡在兜底之外。
"""

from __future__ import annotations

import unittest

from lxml.html import fromstring

from gne import GeneralNewsExtractor
from gne.constants import SITE_NAME_HINTS, SITE_NAME_MAX_LEN
from gne.extractors.title import TitleExtractor

SITE_TITLE = "国家科技管理信息系统公共服务平台"
REAL_TITLE = "关于发布国家重点研发计划2026年度项目申报指南的通知"
BODY = "<article><p>" + "申报单位应在截止时间前完成系统填报。" * 30 + "</p></article>"


def _page(title: str, body: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}{BODY}</body></html>"


class SiteNameDetectionTest(unittest.TestCase):
    def test_site_name_is_recognized(self) -> None:
        self.assertTrue(TitleExtractor._looks_like_site_name(SITE_TITLE))
        for hint in SITE_NAME_HINTS:
            with self.subTest(hint=hint):
                self.assertTrue(TitleExtractor._looks_like_site_name(f"某某{hint}"))

    def test_title_with_separator_is_not_a_site_name(self) -> None:
        # 「标题 - 站点名」这种 <title> 里已经有文章标题，覆盖会把它丢掉。
        self.assertFalse(
            TitleExtractor._looks_like_site_name("关于申报的通知 - 某某门户网站")
        )
        self.assertFalse(TitleExtractor._looks_like_site_name("招标公告 | 某某官网"))
        # 双横线与方括号也是「<title> 里已经有文章标题」的常见写法。
        self.assertFalse(
            TitleExtractor._looks_like_site_name(
                "某某平台--国家科技管理信息系统公共服务平台"
            )
        )
        self.assertFalse(
            TitleExtractor._looks_like_site_name(
                "【国家科技管理信息系统公共服务平台】申报通知"
            )
        )

    def test_real_title_containing_hint_word_is_not_a_site_name(self) -> None:
        # 「平台」「系统」是公文标题的高频词，必须落在结尾才算站点名；
        # 只要求「包含」会把真标题判成站点名，随后被 h1（常是机构名）顶掉，
        # 属于把对的换成错的（评审实测过的退化）。
        self.assertFalse(TitleExtractor._looks_like_site_name("关于XX平台建设的通知"))
        self.assertFalse(TitleExtractor._looks_like_site_name("关于升级系统的通知"))
        self.assertTrue(
            TitleExtractor._looks_like_site_name("国家科技管理信息系统公共服务平台")
        )

    def test_long_text_with_hint_is_not_a_site_name(self) -> None:
        # 只有短串才当站点名，否则含「平台」「系统」的正文式标题会被误判。
        long_title = "关于" + "某某" * SITE_NAME_MAX_LEN + "平台建设的通知"
        self.assertFalse(TitleExtractor._looks_like_site_name(long_title))


class SoleHeadingFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = TitleExtractor()

    def test_site_name_title_is_replaced_by_sole_heading(self) -> None:
        html = _page(SITE_TITLE, f"<h1 class='article__title'>{REAL_TITLE}</h1>")
        title, source = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)
        # 走的还是默认回退链，调用方不应该看到 article_title_xpath。
        self.assertEqual(source, "gne_default")

    def test_no_heading_keeps_site_name(self) -> None:
        title, _ = self.extractor.extract_with_source(fromstring(_page(SITE_TITLE)), "")
        self.assertEqual(title, SITE_TITLE)

    def test_multiple_headings_keep_site_name(self) -> None:
        # 多 h1 常见于门户首页/栏目页，h1 往往是栏目名或推荐位，猜错代价高。
        html = _page(
            SITE_TITLE,
            f"<h1>{REAL_TITLE}</h1><h1>另一个栏目标题</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, SITE_TITLE)

    def test_hidden_heading_does_not_block_fallback(self) -> None:
        # 门户页常见 display:none 的 SEO 占位 h1，把它算进「唯一」会让兜底
        # 在最需要它的页面上永远不生效。
        html = _page(
            SITE_TITLE,
            "<h1 style='display: none'>隐藏栏目导航名称</h1>"
            f"<h1 class='article__title'>{REAL_TITLE}</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_template_heading_does_not_block_fallback(self) -> None:
        # <template> 里的 h1 是模板，不是页面内容。
        html = _page(
            SITE_TITLE,
            "<template><h1>模板里的标题文本</h1></template>"
            f"<h1 class='article__title'>{REAL_TITLE}</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_hidden_ancestor_does_not_block_fallback(self) -> None:
        # 门户页最常见的隐藏写法是外面包一层 display:none 的容器；
        # 只查 h1 自身的 style 会漏掉这一整类。
        html = _page(
            SITE_TITLE,
            "<div style='display:none'><h1>隐藏栏目导航名称</h1></div>"
            f"<h1 class='article__title'>{REAL_TITLE}</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_style_whitespace_variants_do_not_block_fallback(self) -> None:
        # style 属性的空白可能是制表符或换行，只剥半角空格会漏判。
        html = _page(
            SITE_TITLE,
            "<h1 style='display:\tnone'>隐藏栏目导航名称</h1>"
            f"<h1 class='article__title'>{REAL_TITLE}</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_noscript_heading_does_not_block_fallback(self) -> None:
        # <noscript> 里的 h1 只在禁用 JS 时渲染，不是页面标题。
        html = _page(
            SITE_TITLE,
            "<noscript><h1>请启用 JavaScript</h1></noscript>"
            f"<h1 class='article__title'>{REAL_TITLE}</h1>",
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_heading_looking_like_site_name_keeps_site_name(self) -> None:
        # 与 tests/shanxi/1.html 同形：h1 是「山西省人民政府网站」。
        html = _page(SITE_TITLE, "<h1>某某省人民政府网站</h1>")
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, SITE_TITLE)

    def test_short_heading_keeps_site_name(self) -> None:
        html = _page(SITE_TITLE, "<h1>通知</h1>")
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, SITE_TITLE)

    def test_real_title_is_not_replaced_by_heading(self) -> None:
        # <title> 本身是真标题时，h1 不能覆盖它（不是站点名就不进这条路径）。
        html = _page(REAL_TITLE, "<h1>完全不同的另一个标题</h1>")
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, REAL_TITLE)

    def test_og_title_wins_over_sole_heading(self) -> None:
        html = (
            "<html><head>"
            f"<title>{SITE_TITLE}</title>"
            "<meta property='og:title' content='开放图谱里的标题'>"
            f"</head><body><h1>{REAL_TITLE}</h1>{BODY}</body></html>"
        )
        title, _ = self.extractor.extract_with_source(fromstring(html), "")
        self.assertEqual(title, "开放图谱里的标题")

    def test_configured_title_xpath_has_highest_priority(self) -> None:
        html = _page(SITE_TITLE, "<h1>别的标题文本</h1><h2 class='t'>XPath 标题</h2>")
        title, source = self.extractor.extract_with_source(fromstring(html), "//h2")
        self.assertEqual(title, "XPath 标题")
        self.assertEqual(source, "article_title_xpath")


class SiteNameFallbackEndToEndTest(unittest.TestCase):
    def test_engine_returns_sole_heading_for_site_name_title(self) -> None:
        # 端到端走 GeneralNewsExtractor，确认 process_entry 拿到的就是真标题。
        html = _page(SITE_TITLE, f"<h1 class='article__title'>{REAL_TITLE}</h1>")
        result = GeneralNewsExtractor().extract(html)
        self.assertEqual(result["title"], REAL_TITLE)
        self.assertEqual(result["title_source"], "gne_default")

    def test_engine_keeps_site_name_when_no_heading(self) -> None:
        result = GeneralNewsExtractor().extract(_page(SITE_TITLE))
        self.assertEqual(result["title"], SITE_TITLE)


if __name__ == "__main__":
    unittest.main()
