"""Title extraction from XPath, headings, Open Graph and JSON-LD."""

from __future__ import annotations

import json
import re

from lxml.html import HtmlElement

from ..config import config
from ..constants import (
    TITLE_HTAG_AND_TITLE_XPATH,
    TITLE_HTAG_XPATH,
    TITLE_SPLIT_CHAR_PATTERN,
)
from ..text import get_longest_common_sub_string


class TitleExtractor:
    """Extract a news title with the legacy fallback chain."""

    def extract_by_xpath(self, element: HtmlElement, title_xpath: str) -> str:
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

    def extract(self, element: HtmlElement, title_xpath: str = "") -> str:
        """Extract the title using the same priority order as before."""
        title_xpath = title_xpath or config.get("title", {}).get("xpath")
        title = (
            self.extract_by_xpath(element, title_xpath)
            or self.extract_by_htag_and_title(element)
            or self.extract_by_og_title(element)
            or self.extract_by_json_ld(element)
            or self.extract_by_title(element)
            or self.extract_by_htag(element)
        )
        return title.strip()
