"""Main article-body extraction using the legacy text-density algorithm."""

from __future__ import annotations

import json
import math
import re
from html import unescape
from typing import Any

from lxml.html import HtmlElement, etree

from ..config import config
from ..html import (
    BR_SENTINEL,
    get_high_weight_keyword_pattern,
    iter_node,
    pad_host_for_images,
)

PUNCTUATION = frozenset({
    chr(code)
    for code in (
        0x21,
        0x22,
        0x25,
        0x27,
        0x28,
        0x29,
        0x2C,
        0x2E,
        0x3A,
        0x3B,
        0x3F,
        0x3001,
        0x3002,
        0x300A,
        0x300B,
        0xFF01,
        0xFF08,
        0xFF09,
        0xFF0C,
        0xFF1A,
        0xFF1B,
        0xFF1F,
    )
})


class ContentExtractor:
    """Extract the densest article body from a parsed HTML document."""

    def __init__(self, content_tag: str = "p") -> None:
        """Create an extractor whose body text is expected in content_tag."""
        self.content_tag = content_tag
        self.node_info: dict[int, dict[str, Any]] = {}
        self.high_weight_keyword_pattern = get_high_weight_keyword_pattern()
        self.punctuation = PUNCTUATION
        self.element_text_cache: dict[str, list[str]] = {}

    def reset(self) -> None:
        self.node_info = {}
        self.element_text_cache = {}

    def extract(
        self,
        selector: HtmlElement,
        host: str = "",
        body_xpath: str = "",
        with_body_html: bool = False,
        use_visiable_info: bool = False,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return candidate body nodes sorted by their density score."""
        self.reset()
        body_xpath = body_xpath or config.get("body", {}).get("xpath", "")
        use_visiable_info = use_visiable_info or config.get("use_visiable_info", False)
        if body_xpath:
            body_list = selector.xpath(body_xpath)
            body = body_list[0] if body_list else selector.xpath("//body")[0]
        else:
            article_body = selector.xpath('//*[@itemprop="articleBody"]')
            if article_body:
                body = article_body[0]
            else:
                body_list = selector.xpath("//body")
                if not body_list:
                    return []
                body = body_list[0]

        host = host or config.get("host", "")
        include_body_html = with_body_html or config.get("with_body_html", False)
        for node in iter_node(body):
            if use_visiable_info:
                if not node.attrib.get("is_visiable", True):
                    continue
                coordinate = json.loads(node.attrib.get("coordinate", "{}"))
                if coordinate.get("height", 0) < 150:
                    continue

            density_info = self.calc_text_density(node)
            ti_text = density_info["ti_text"]
            sbdi = self.calc_sbdi(ti_text, density_info["ti"], density_info["lti"])
            images_list = node.xpath(".//img/@src")
            if host:
                images_list = [pad_host_for_images(host, url) for url in images_list]

            node_info: dict[str, Any] = {
                "ti": density_info["ti"],
                "lti": density_info["lti"],
                "tgi": density_info["tgi"],
                "ltgi": density_info["ltgi"],
                "node": node,
                "density": density_info["density"],
                "text": self.get_display_text_of_element(node),
                "images": images_list,
                "text_tag_count": self.count_text_tag(node, tag="p"),
                "sbdi": sbdi,
            }
            if use_visiable_info:
                node_info["is_visiable"] = node.attrib["is_visiable"]
                node_info["coordinate"] = node.attrib.get("coordinate", "")
            if include_body_html:
                node_info["body_html"] = unescape(
                    etree.tostring(node, encoding="utf-8").decode()
                ).replace(BR_SENTINEL, "\n")
            self.node_info[hash(node)] = node_info

        self.calc_new_score()
        return sorted(self.node_info.items(), key=lambda item: item[1]["score"], reverse=True)

    def count_text_tag(self, element: HtmlElement, tag: str = "p") -> int:
        """Count descendant tag elements plus direct text nodes."""
        tag_num = len(element.xpath(f".//{tag}"))
        direct_text = len(element.xpath("text()"))
        return tag_num + direct_text

    def get_all_text_of_element(self, element_list: HtmlElement | list[HtmlElement]) -> list[str]:
        """Return cleaned text fragments, caching by document XPath."""
        if not isinstance(element_list, list):
            element_list = [element_list]

        text_list: list[str] = []
        for element in element_list:
            element_flag = element.getroottree().getpath(element)
            if element_flag in self.element_text_cache:
                text_list.extend(self.element_text_cache[element_flag])
            else:
                element_text_list: list[str] = []
                for text in element.xpath(".//text()"):
                    text = text.strip()
                    if not text:
                        continue
                    clear_text = re.sub(" +", " ", text, flags=re.S)
                    clear_text = clear_text.replace("\n", "").replace(
                        BR_SENTINEL, ""
                    )
                    if not clear_text:
                        continue
                    element_text_list.append(clear_text)
                self.element_text_cache[element_flag] = element_text_list
                text_list.extend(element_text_list)
        return text_list

    def get_display_text_of_element(self, element: HtmlElement) -> str:
        """Return body text with real <br> markers restored as newlines."""
        fragments: list[str] = []
        for text in element.xpath(".//text()"):
            text = text.strip()
            if not text:
                continue
            clear_text = (
                re.sub(" +", " ", text, flags=re.S)
                .replace("\n", "")
                .replace(BR_SENTINEL, "\n")
                .strip("\n")
            )
            if clear_text:
                fragments.append(clear_text)
        return "\n".join(fragments)

    def need_skip_ltgi(self, ti: int, lti: int) -> bool:
        """Return whether link text is too sparse to exclude from the density."""
        if lti == 0:
            return False
        return ti // lti > 10

    def calc_text_density(self, element: HtmlElement) -> dict[str, Any]:
        """Calculate the legacy text density for a single element."""
        ti_text = "\n".join(self.get_all_text_of_element(element))
        ti = len(ti_text)
        ti = self.increase_tag_weight(ti, element)
        a_tag_list = element.xpath(".//a")

        lti = len("".join(self.get_all_text_of_element(a_tag_list)))
        tgi = len(element.xpath(".//*"))
        ltgi = len(a_tag_list)
        if (tgi - ltgi) == 0:
            if not self.need_skip_ltgi(ti, lti):
                return {
                    "density": 0,
                    "ti_text": ti_text,
                    "ti": ti,
                    "lti": lti,
                    "tgi": tgi,
                    "ltgi": ltgi,
                }
            ltgi = 0
        density = (ti - lti) / (tgi - ltgi)
        return {
            "density": density,
            "ti_text": ti_text,
            "ti": ti,
            "lti": lti,
            "tgi": tgi,
            "ltgi": ltgi,
        }

    def increase_tag_weight(self, ti: int, element: HtmlElement) -> int:
        """Double the text length when the class name looks like article body."""
        tag_class = element.get("class", "")
        if self.high_weight_keyword_pattern.search(tag_class):
            return 2 * ti
        return ti

    def calc_sbdi(self, text: str, ti: int, lti: int) -> float:
        """Calculate the legacy symbol density, never returning zero."""
        sbi = self.count_punctuation_num(text)
        sbdi = (ti - lti) / (sbi + 1)
        return sbdi or 1

    def count_punctuation_num(self, text: str) -> int:
        return sum(1 for char in text if char in self.punctuation)

    def calc_new_score(self) -> None:
        """Compute the final score for every candidate node in place."""
        for node_hash, node_info in self.node_info.items():
            score = (
                node_info["density"]
                * math.log10(node_info["text_tag_count"] + 2)
                * math.log(node_info["sbdi"])
            )
            self.node_info[node_hash]["score"] = score
