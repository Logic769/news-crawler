"""DOM cleanup and URL helpers for the extraction engine."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from lxml.html import HtmlElement, etree, fromstring

from .config import config
from .constants import (
    HIGH_WEIGHT_ATTR_KEYWORD,
    TAGS_CAN_BE_REMOVE_IF_EMPTY,
    USELESS_ATTR,
    USELESS_TAG,
)

# 只匹配真正的 <br>/<br/>：\b 防止误伤 <brand> 这类以 br 开头的标签。
_BR_RE = re.compile(r"</?br\b[^>]*>", re.I)
HIGH_WEIGHT_KEYWORD_RE = re.compile("|".join(HIGH_WEIGHT_ATTR_KEYWORD), flags=re.I)


def _is_useless_node(class_name: str, id_name: str) -> bool:
    """Return whether a class/id value marks the node as page furniture.

    Matching is token based: split class/id on spaces and then on ``-`` or
    ``_`` so short keywords such as ``ad`` or ``nav`` cannot match inside an
    unrelated word.
    """
    for value in (class_name, id_name):
        if not value:
            continue
        for token in value.lower().split():
            if token in USELESS_ATTR:
                return True
            if any(part in USELESS_ATTR for part in re.split(r"[-_]+", token)):
                return True
    return False


def normalize_node(element: HtmlElement) -> None:
    """Remove unusable tags and page furniture from ``element`` in place."""
    etree.strip_elements(element, *USELESS_TAG)
    nodes_to_remove: list[HtmlElement] = []
    for node in list(iter_node(element)):
        if node.tag.lower() in TAGS_CAN_BE_REMOVE_IF_EMPTY and is_empty_element(node):
            remove_node(node)
            continue

        if node.tag.lower() == "p":
            etree.strip_tags(node, "span")
            etree.strip_tags(node, "strong")

        if node.tag.lower() == "div" and not node.getchildren():
            node.tag = "p"

        if node.tag.lower() == "span" and not node.getchildren():
            node.tag = "p"

        if node.tag.lower() == "p" and not node.xpath(".//img"):
            if not (node.text and node.text.strip()):
                drop_tag(node)
                continue

        class_name = node.get("class", "")
        id_name = node.get("id", "")
        if class_name or id_name:
            if node.tag.lower() not in ("body", "html") and _is_useless_node(class_name, id_name):
                nodes_to_remove.append(node)

    for node in nodes_to_remove:
        remove_node(node)


def html2element(html: str) -> HtmlElement:
    """Parse an HTML fragment, turning ``br`` tags into line breaks.

    以前直接删除 ``<br>`` 会让相邻文本粘连（"foo<br>bar" → "foobar"），
    这里替换为换行，保留断行语义；后续文本归一化会把多余空白压平。
    """
    return fromstring(_BR_RE.sub("\n", html))


def pre_parse(element: HtmlElement) -> HtmlElement:
    """Normalize a parsed document before content extraction."""
    normalize_node(element)
    return element


def remove_noise_node(element: HtmlElement, noise_xpath_list: list[str] | None = None) -> HtmlElement | None:
    """Remove nodes matched by user or ``.gne`` configured XPath expressions."""
    noise_xpath_list = noise_xpath_list or config.get("noise_node_list")
    if not noise_xpath_list:
        return None
    for noise_xpath in noise_xpath_list:
        for node in element.xpath(noise_xpath):
            remove_node(node)
    return element


def iter_node(element: HtmlElement):
    """Yield ``element`` and all descendant ``HtmlElement`` objects."""
    yield element
    for sub_element in element:
        if isinstance(sub_element, HtmlElement):
            yield from iter_node(sub_element)


def remove_node(node: HtmlElement) -> None:
    """Remove ``node`` from its parent when it has one."""
    parent = node.getparent()
    if parent is not None:
        parent.remove(node)


def drop_tag(node: HtmlElement) -> None:
    """Drop the tag while keeping its text in the parent."""
    if node.getparent() is not None:
        node.drop_tag()


def is_empty_element(node: HtmlElement) -> bool:
    """Return whether a node has neither children nor direct text."""
    return not node.getchildren() and not node.text


def pad_host_for_images(host: str, url: str) -> str:
    """Turn a relative image URL into an absolute URL."""
    if url.startswith("http"):
        return url
    parsed_uri = urlparse(host)
    scheme = parsed_uri.scheme
    if url.startswith(":"):
        return f"{scheme}{url}"
    if url.startswith("//"):
        return f"{scheme}:{url}"
    return urljoin(host, url)


def get_high_weight_keyword_pattern() -> re.Pattern[str]:
    """Return the compiled class-name pattern used for content weighting."""
    return HIGH_WEIGHT_KEYWORD_RE


def fix_html(html: str) -> str:
    """Move any misplaced ``</html>`` to the end of the document."""
    new_html = html.replace("</html>", "")
    return f"{new_html}</html>"
