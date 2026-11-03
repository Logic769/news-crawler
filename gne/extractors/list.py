"""List-page extraction helpers kept for public API compatibility."""

from __future__ import annotations

from collections import deque

from lxml.html import HtmlElement

from ..html import html2element
from ..text import normalize_text


class ListExtractor:
    """Extract repeated list items around a feature node.

    This is the historical experimental helper. Its traversal and stopping
    rules are intentionally unchanged: walk up from the feature node until a
    parent contains more than three matching children, then read each child's
    text and optional href.
    """

    def extract(self, element: HtmlElement, feature: str) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if feature.startswith("/"):
            feature_element = element.xpath(feature)
        else:
            feature_element = element.xpath(f'//*[contains(text(), "{feature}")]')

        if not feature_element:
            print("找不到 feature！")
            return result

        parent = feature_element[0]
        leaf_class = parent.attrib.get("class", "")
        if leaf_class:
            leaf_node = f'{parent.tag}[@class="{leaf_class}"]'
        else:
            leaf_node = parent.tag
        is_a_tag = parent.tag == "a"
        sub_path_queue = deque([leaf_node])
        while parent is not None:
            parent = parent.getparent()
            if parent is None:
                break
            path = "/".join(sub_path_queue)
            item_list = parent.xpath(path)
            if len(item_list) > 3:
                for item in item_list:
                    item_info = {"title": "".join(item.xpath("text()"))}
                    if is_a_tag:
                        item_info["url"] = "".join(item.xpath("@href"))
                    result.append(item_info)
                return result
            sub_path_queue.insert(0, parent.tag)
        return result


class ListPageExtractor:
    """Public wrapper that parses HTML text before using ListExtractor."""

    def extract(self, html: str, feature: str) -> list[dict[str, str]]:
        element = html2element(normalize_text(html))
        return ListExtractor().extract(element, feature)
