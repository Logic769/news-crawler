"""Extractor for document meta tags."""

from __future__ import annotations

from lxml.html import HtmlElement


class MetaExtractor:
    """Collect name/property meta values into a single mapping."""

    def extract(self, element: HtmlElement) -> dict[str, str]:
        meta_content: dict[str, str] = {}
        for meta in element.xpath("//meta"):
            name = meta.xpath("@name|@property")
            if not name:
                continue
            content = meta.xpath("@content")
            if not content:
                continue
            meta_content[name[0]] = content[0]
        return meta_content
