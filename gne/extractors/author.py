"""Author extraction from structured data, meta tags and page text."""

from __future__ import annotations

import json

from lxml.html import HtmlElement

from ..config import config
from ..constants import AUTHOR_PATTERN


class AuthorExtractor:
    """Extract an article author using the legacy priority order."""

    def __init__(self) -> None:
        self.author_pattern = AUTHOR_PATTERN

    @staticmethod
    def _is_valid_author(text: str) -> bool:
        """Return whether the text looks like a real author name."""
        if not text:
            return False
        if text.isdigit():
            return False
        if "@" in text:
            return False
        if text.lower() in ("name", "author", "admin", "editor", "test"):
            return False
        if len(text) < 2 or len(text) > 30:
            return False
        return True

    def extract_from_json_ld(self, element: HtmlElement) -> str:
        """Read an author from JSON-LD author fields."""
        for script_text in element.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                data = json.loads(script_text.strip())
                if isinstance(data, list):
                    data = data[0]
                if not isinstance(data, dict):
                    continue
                author = data.get("author")
                if not author:
                    continue
                if isinstance(author, str):
                    if self._is_valid_author(author):
                        return author
                elif isinstance(author, dict):
                    name = author.get("name", "")
                    if self._is_valid_author(name):
                        return name
                elif isinstance(author, list) and author:
                    first = author[0]
                    name = first.get("name", "") if isinstance(first, dict) else str(first)
                    if self._is_valid_author(name):
                        return name
            except (json.JSONDecodeError, IndexError, TypeError):
                continue
        return ""

    def extract(self, element: HtmlElement, author_xpath: str = "") -> str:
        """Extract the author from XPath, JSON-LD, meta tags or text."""
        author_xpath = author_xpath or config.get("author", {}).get("xpath")
        if author_xpath:
            return "".join(element.xpath(author_xpath))

        json_ld_author = self.extract_from_json_ld(element)
        if json_ld_author:
            return json_ld_author

        meta_author = element.xpath('//meta[@name="author"]/@content')
        if meta_author:
            author = meta_author[0].strip()
            if self._is_valid_author(author):
                return author

        itemprop_author = element.xpath('//*[@itemprop="author"]//text()')
        if itemprop_author:
            author = "".join(itemprop_author).strip()
            if self._is_valid_author(author):
                return author

        class_author = element.xpath('//*[@class="author"]//text() | //a[@rel="author"]//text()')
        if class_author:
            author = "".join(class_author).strip()
            if self._is_valid_author(author):
                return author

        text = "".join(element.xpath(".//text()"))
        for pattern in self.author_pattern:
            author_obj = pattern.search(text)
            if author_obj:
                return author_obj.group(1)
        return ""

    # 保留旧方法名，兼容已有调用方。
    extractor = extract
