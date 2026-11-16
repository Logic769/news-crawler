"""Main article-body extraction using the legacy text-density algorithm."""

from __future__ import annotations

import json
import logging
import math
import re
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


LOG = logging.getLogger(__name__)


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

    @staticmethod
    def _resolve_body(selector: HtmlElement, body_xpath: str) -> HtmlElement:
        """按配置的 body_xpath 选正文容器，未命中或不是元素节点时回退整页 //body。

        body_xpath 来自站点配置（手写 XPath），写错、写成属性/文本表达式
        （如 //div/@id、//div/text()）或标量表达式（如 true()、
        1 + count(//div)）时不能让它把整篇文章变成 error，因此这里统一兜底：
        非节点列表、非元素结果与空结果都回退 //body，并留一条告警，
        便于区分「配错」与「没配」。
        """
        fallback: list[HtmlElement] = selector.xpath("//body") or [selector]
        try:
            matched = selector.xpath(body_xpath)
        except Exception as exc:  # 运行期 XPath 异常（如非法函数组合）
            LOG.warning("body_xpath %r 执行失败（%s），回退整页 //body", body_xpath, exc)
            return fallback[0]
        # lxml 对布尔/数值表达式的返回值不是列表（true() 得 bool、1 + count(//div)
        # 得 float），直接下标会抛 TypeError；它又发生在 try 之外，会一路抛到 extract，
        # 与「配置写错不中断整篇抽取」的约定相反，因此先判类型。
        if not isinstance(matched, list):
            LOG.warning(
                "body_xpath %r 的结果不是节点列表（%s），回退整页 //body",
                body_xpath,
                type(matched).__name__,
            )
            return fallback[0]
        node = matched[0] if matched else None
        if node is None:
            LOG.warning("body_xpath %r 未命中任何节点，回退整页 //body", body_xpath)
            return fallback[0]
        # 宽松表达式（如 //div[contains(@class,'content')]）在真实站点上常常
        # 命中侧栏/导航等多个容器，只取第一个就会把侧栏当正文以 status=ok 入库。
        # 语义保持不变（仍取第一个），但必须告警，否则事后无从发现。
        if len(matched) > 1:
            LOG.warning(
                "body_xpath %r 命中 %d 个节点，只使用第一个；建议改写成唯一元素表达式",
                body_xpath,
                len(matched),
            )
        # 属性/文本节点（_ElementUnicodeResult）没有 getroottree/tag 语义，
        # 后续密度算法会直接抛 AttributeError，这里提前拦下。
        if not isinstance(node, HtmlElement):
            LOG.warning(
                "body_xpath %r 命中的不是元素节点（%s），请改写成元素表达式，已回退整页 //body",
                body_xpath,
                type(node).__name__,
            )
            return fallback[0]
        # 命中的容器没有可见文本（改版后容器仍在但内容被挪走）时同样回退，
        # 否则正文恒为空，整站会一直记 no_content。
        if not (node.text_content() or "").strip():
            LOG.warning(
                "body_xpath %r 命中的容器没有文本内容，回退整页 //body", body_xpath
            )
            return fallback[0]
        return node

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
            body = self._resolve_body(selector, body_xpath)
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
                node_info["is_visiable"] = node.attrib.get("is_visiable", True)
                node_info["coordinate"] = node.attrib.get("coordinate", "")
            if include_body_html:
                # 不能用 html.unescape 解码序列化结果：正文里本来就有的
                # “&lt;div&gt;”文本会被还原成真标签，既破坏文本也绕过清洗边界。
                node_info["body_html"] = etree.tostring(
                    node, encoding="unicode", method="html"
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
