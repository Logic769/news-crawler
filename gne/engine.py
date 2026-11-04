"""Public article extraction entry point."""

from __future__ import annotations

from typing import Any

from .config import config
from .exceptions import NoContentException
from .extractors import (
    AuthorExtractor,
    ContentExtractor,
    MetaExtractor,
    TimeExtractor,
    TitleExtractor,
)
from .html import BR_SENTINEL, fix_html, html2element, pre_parse, remove_noise_node
from .text import normalize_text


class GeneralNewsExtractor:
    """Extract title, author, publish time, body text and images from HTML.

    extract() 不依赖实例级可变状态：每次调用都新建 ContentExtractor（正文密度
    算法内部有 node_info/element_text_cache 两个可变缓存），因此同一个
    GeneralNewsExtractor 实例可以跨线程复用。
    """

    def __init__(self) -> None:
        self._title_extractor = TitleExtractor()
        self._time_extractor = TimeExtractor()
        self._author_extractor = AuthorExtractor()
        self._meta_extractor = MetaExtractor()

    def extract(
        self,
        html: str,
        title_xpath: str = "",
        author_xpath: str = "",
        publish_time_xpath: str = "",
        host: str = "",
        body_xpath: str = "",
        normalize: bool = False,
        noise_node_list: list[str] | None = None,
        with_body_html: bool = False,
        use_visiable_info: bool = False,
    ) -> dict[str, Any]:
        """Extract article fields from a raw HTML string.

        The preprocessing order intentionally matches the legacy behavior:
        XPath-based fields are read before noise nodes are removed, because
        normalization may destroy the structure those XPath expressions rely
        on.
        """
        html = fix_html(html)
        normal_html = normalize_text(html) if normalize else html
        element = html2element(normal_html)

        meta_content = self._meta_extractor.extract(element)
        title = self._title_extractor.extract(
            element, title_xpath=title_xpath
        ).replace(BR_SENTINEL, "\n").strip()
        publish_time = self._time_extractor.extract(
            element, publish_time_xpath=publish_time_xpath
        ).replace(BR_SENTINEL, "\n").strip()
        author = self._author_extractor.extract(
            element, author_xpath=author_xpath
        ).replace(BR_SENTINEL, "\n").strip()

        remove_noise_node(element, noise_node_list)
        element = pre_parse(element)

        content = ContentExtractor().extract(
            element,
            host=host,
            with_body_html=with_body_html,
            body_xpath=body_xpath,
            use_visiable_info=use_visiable_info,
        )
        if not content:
            raise NoContentException("无法提取正文！")

        result: dict[str, Any] = {
            "title": title,
            "author": author,
            "publish_time": publish_time,
            "content": content[0][1]["text"],
            "images": content[0][1]["images"],
            "meta": meta_content,
        }
        if with_body_html or config.get("with_body_html", False):
            result["body_html"] = content[0][1]["body_html"]
        return result
