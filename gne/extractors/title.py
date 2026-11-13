"""Title extraction from XPath, headings, Open Graph and JSON-LD."""

from __future__ import annotations

import json
import logging
import re

from lxml.html import HtmlElement

from ..config import config
from ..constants import (
    SITE_FALLBACK_MAX_LEN,
    SITE_FALLBACK_MIN_LEN,
    SITE_NAME_HINTS,
    SITE_NAME_MAX_LEN,
    TITLE_HTAG_AND_TITLE_XPATH,
    TITLE_HTAG_XPATH,
    TITLE_SPLIT_CHAR_PATTERN,
)
from ..text import get_longest_common_sub_string

LOG = logging.getLogger(__name__)


def _normalized_style(node: HtmlElement) -> str:
    """把内联 style 归一成无空白的小写串，便于子串判断。"""
    return re.sub(r"\s+", "", node.get("style") or "").lower()


class TitleExtractor:
    """Extract a news title with the legacy fallback chain."""

    def extract_by_xpath(self, element: HtmlElement, title_xpath: str) -> str:
        """Return the text matched by title_xpath; empty string when it is unusable."""
        if not title_xpath:
            return ""
        try:
            title_list = element.xpath(title_xpath)
        except Exception:  # 非法 XPath 不该让整篇文章变成 error
            return ""
        # XPath 1.0 的标量结果：字符串可直接用（normalize-space(//h1) 等），
        # 数字/布尔没有文本语义，返回空串交给下一级回退，绝不能当成序列索引。
        if isinstance(title_list, str):
            return title_list.strip()
        if not isinstance(title_list, list) or not title_list:
            return ""
        value = title_list[0]
        if isinstance(value, str):
            return value.strip()
        # 元素节点兜底：配置写成 //h1 而不是 //h1/text() 时取它的全部文本。
        # 直接返回元素会让上层 .replace() 撞上 lxml 的 _Element.replace() 抛 AttributeError。
        text = value.text_content() if hasattr(value, "text_content") else ""
        return (text or "").strip()

    def extract_by_title(self, element: HtmlElement) -> str:
        title_list = element.xpath("//title/text()")
        if not title_list:
            return ""
        title = re.split(TITLE_SPLIT_CHAR_PATTERN, title_list[0])
        if title:
            if len(title[0]) >= 4:
                return title[0]
            return title_list[0]
        return ""

    def extract_by_htag(self, element: HtmlElement) -> str:
        title_list = element.xpath(TITLE_HTAG_XPATH)
        if not title_list:
            return ""
        return title_list[0]

    def extract_by_htag_and_title(self, element: HtmlElement) -> str:
        """Find the heading text sharing the longest substring with the title."""
        h_tag_texts_list = element.xpath(TITLE_HTAG_AND_TITLE_XPATH)
        title_text = "".join(element.xpath("//title/text()"))
        news_title = ""
        for h_tag_text in h_tag_texts_list:
            lcs = get_longest_common_sub_string(title_text, h_tag_text)
            if len(lcs) > len(news_title):
                news_title = lcs
        return news_title if len(news_title) > 4 else ""

    def extract_by_og_title(self, element: HtmlElement) -> str:
        og_title = element.xpath('//meta[@property="og:title"]/@content')
        if og_title:
            return og_title[0].strip()
        twitter_title = element.xpath('//meta[@name="twitter:title"]/@content')
        if twitter_title:
            return twitter_title[0].strip()
        dc_title = element.xpath('//meta[@property="dc:title"]/@content')
        if dc_title:
            return dc_title[0].strip()
        return ""

    def extract_by_json_ld(self, element: HtmlElement) -> str:
        for script_text in element.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                data = json.loads(script_text.strip())
                if isinstance(data, list):
                    data = data[0]
                if isinstance(data, dict):
                    headline = data.get("headline") or data.get("name", "")
                    if headline and len(headline) >= 4:
                        return headline.strip()
            except (json.JSONDecodeError, IndexError, TypeError):
                continue
        return ""

    @staticmethod
    def _looks_like_site_name(text: str) -> bool:
        """<title> 是否只是站点名，而不是文章标题。

        政务 CMS 常把 <title> 写成「XX省人民政府门户网站」这种固定串，
        真标题只出现在正文 h1 里。这里做保守判断：短、以站点类词汇结尾、且
        没有「标题 - 站点名」这种常见真标题分隔符——带了分隔符说明
        <title> 里已经有文章标题，不能覆盖。

        站点类词汇必须落在**结尾**，不能只要求「包含」：「平台」「系统」是
        公文标题的高频词，「关于XX平台建设的通知」这类真标题若被判成站点名，
        就会被 h1（常是机构名）顶掉，属于把对的换成错的。
        """
        text = " ".join(text.split())
        if not text or len(text) > SITE_NAME_MAX_LEN:
            return False
        if re.search(r"\s[-|_]\s|--|【|】", text):
            return False
        return any(text.endswith(hint) for hint in SITE_NAME_HINTS)

    @staticmethod
    def _raw_title(element: HtmlElement) -> str:
        """<title> 的原始文本，不按 TITLE_SPLIT_CHAR_PATTERN 切分。

        站点名判断必须看完整 <title>：extract_by_title 会把「文章标题 - 站点名」
        切成前半段，切完的片段既看不到分隔符、又更容易以站点类词汇结尾，于是
        「关于启用申报平台 - 某某官网」会被误判成站点名，反过来用机构名 h1
        覆盖掉本来正确的文章标题。
        """
        return " ".join("".join(element.xpath("//title/text()")).split())

    @staticmethod
    def _is_hidden_heading(node: HtmlElement) -> bool:
        """h1 自身或任一祖先不可见时，不能当作页面标题。

        门户/详情页最常见的隐藏写法是外面包一层 display:none 的容器
        （右侧栏、SEO 块、未激活的 Tab 面板），只查 h1 自身会漏掉这一整类。
        style 里的空白可能是制表符或换行，统一把所有空白字符剥掉再匹配。

        分两类处理：hidden 属性、aria-hidden="true"、display:none 都不能由后代
        恢复，出现在 h1 自身或任一祖先上都判隐藏；visibility 会被后代覆盖
        （祖先 visibility:hidden + h1 visibility:visible 时 h1 实际可见），
        所以只看离 h1 最近的显式声明，声明成 hidden/collapse 才算隐藏。

        按 class/外部样式表隐藏（如 sr-only）不在内联 style 里，无法判定，属已知取舍。
        """
        for current in (node, *node.iterancestors()):
            if current.get("hidden") is not None:
                return True
            if (current.get("aria-hidden") or "").strip().lower() == "true":
                return True
            if "display:none" in _normalized_style(current):
                return True
        for current in (node, *node.iterancestors()):
            match = re.search(
                r"visibility:(visible|hidden|collapse)", _normalized_style(current)
            )
            if match:
                return match.group(1) != "visible"
        return False

    def extract_by_sole_heading(self, element: HtmlElement) -> str:
        """页面只有一个可见的标题级 h1、且它不像站点名时，用它兜底。

        只在 <title> 被判定为站点名时由 extract_with_source 调用：那时
        回退链拿不到任何可信标题，唯一 h1 通常就是文章标题。要求「唯一」
        是因为多 h1 页面里 h1 常是栏目标题或关键词堆砌，猜错代价更高。
        `display:none` 的 SEO 占位 h1、`<template>`/`<noscript>` 里的模板 h1
        不计入唯一性——门户页常常正好带一个隐藏 h1，计进来就永远凑不成唯一。
        """
        headings: list[str] = []
        for node in element.xpath(
            "//h1[not(ancestor::template) and not(ancestor::noscript)]"
        ):
            if self._is_hidden_heading(node):
                continue
            text = " ".join(node.text_content().split())
            if text:
                headings.append(text)
        if len(headings) != 1:
            return ""
        heading = headings[0]
        if not SITE_FALLBACK_MIN_LEN <= len(heading) <= SITE_FALLBACK_MAX_LEN:
            return ""
        if self._looks_like_site_name(heading):
            return ""
        return heading

    def extract(self, element: HtmlElement, title_xpath: str = "") -> str:
        """Extract the title using the same priority order as before."""
        return self.extract_with_source(element, title_xpath)[0]

    def extract_with_source(
        self, element: HtmlElement, title_xpath: str = ""
    ) -> tuple[str, str]:
        """Return (title, source) so callers can tell an XPath hit from a fallback."""
        title_xpath = title_xpath or config.get("title", {}).get("xpath")
        title = self.extract_by_xpath(element, title_xpath)
        if title:
            return title.strip(), (
                "article_title_xpath" if title_xpath else "gne_default"
            )
        title = (
            self.extract_by_htag_and_title(element)
            or self.extract_by_og_title(element)
            or self.extract_by_json_ld(element)
        )
        if not title:
            # <title> 只是站点名时（政务 CMS 常见），原来的回退链会把它当成
            # 文章标题。这类页面真标题往往就在唯一 h1 里，先试 h1 再退回 <title>。
            # 站名判断必须用未切分的原始 <title>：extract_by_title 会按「- _ |」切成
            # 前半段，「关于启用申报平台 - 某某官网」切完只剩「关于启用申报平台」，
            # 既看不到分隔符又恰好以「平台」结尾，会被误判成站点名而丢掉正确标题。
            by_title = self.extract_by_title(element)
            if by_title and self._looks_like_site_name(self._raw_title(element)):
                sole_heading = self.extract_by_sole_heading(element)
                if sole_heading:
                    # 底层库不逐篇刷 INFO：批量抓取时每命中一篇就是一条。
                    LOG.debug("<title> 是站点名，改用页面唯一 h1 作为标题：%r", sole_heading)
                    title = sole_heading
            title = title or by_title
        title = title or self.extract_by_htag(element)
        return title.strip(), "gne_default"
