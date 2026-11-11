"""Publish-time extraction from XPath, meta, JSON-LD, time tags and text."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re

from lxml.html import HtmlElement

from ..config import config
from ..constants import DATETIME_PATTERN, PUBLISH_TIME_META

LOG = logging.getLogger(__name__)

_DATE_SEP_RE = re.compile(r"[-/.]")
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "div", "dl",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "html", "li", "main", "nav",
        "ol", "p", "section", "table", "td", "th", "tr", "ul",
    }
)
# script/style 里的日期是脚本变量，不是页面展示的发布时间；扫描文本时必须排除。
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe", "footer", "nav"})
_SIDEBAR_HINTS = ("sidebar", "related", "recommend", "advert", "ad-", " nav")
_ISO_DATETIME_RE = re.compile(
    r"((?:19|20)\d{2}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|z|[+-]\d{2}:?\d{2})?)"
)
# “发布时间：2026年11月11日”这类带标签的日期，比正文里的时间更可信。
# 标签与日期之间最多看 30 个字符，避免把后文无关日期带进来。
_LABELED_DATE_RE = re.compile(
    r"(?:发布时间|发布日期|发布于|更新时间)[：: \t]{0,4}"
    r"([^。；\n，,、；;|]{0,30})"
)
# 标签后常见“详见附件”“会议于…”等非日期内容，遇到即截断窗口。
_LABELED_STOP_WORDS = re.compile(
    r"(?:详见|附件|会议|举行|活动|报名|召开|地点|联系|电话)"
)


class TimeExtractor:
    """Extract an article publish time with the legacy priority order."""

    def __init__(self) -> None:
        self.time_pattern = DATETIME_PATTERN

    def extract(self, element: HtmlElement, publish_time_xpath: str = "") -> str:
        return self.extract_with_source(element, publish_time_xpath)[0]

    def extract_with_source(
        self, element: HtmlElement, publish_time_xpath: str = ""
    ) -> tuple[str, str]:
        """Return (value, source) so callers can record whether the XPath really hit."""
        publish_time_xpath = publish_time_xpath or config.get("publish_time", {}).get("xpath")
        # 用户显式配置的 XPath 永远是第一优先级，且先于任何全文档扫描。
        xpath_value = self.extract_from_user_xpath(publish_time_xpath, element)
        if xpath_value:
            return xpath_value, "xpath"
        labeled = self.extract_from_labeled_text(element)
        # 标签日期已经带时分秒时无需再全文扫描；只有标签只有日期时，
        # 才继续找同一天更精确的正文时间（例如可见正文里的完整时间）。
        if labeled and self._has_time(labeled):
            LOG.debug("标签发布时间已含时分，跳过全文时间扫描: %s", labeled)
            text = ""
        else:
            text = self.extract_from_text(element)
        meta = self.extract_from_meta(element, include_update=False)
        meta_update = self.extract_from_meta(element, only_update=True)
        json_ld = self.extract_from_json_ld(element)
        time_tag = self.extract_from_time_tag(element)
        # 页面可见的“发布时间：”是明确语义，不能被模板注入的不同日期覆盖；
        # 但若同一日期在 meta/json_ld/time_tag/text 中有更精确的时分，则取更精确值。
        if labeled:
            if self._has_time(labeled):
                return labeled, "labeled_text"
            for candidate, candidate_source in (
                (meta, "meta"),
                (json_ld, "json_ld"),
                (time_tag, "time_tag"),
                (text, "text"),
            ):
                if (
                    candidate
                    and self._same_date(labeled, candidate)
                    and self._has_time(candidate)
                ):
                    return candidate, candidate_source
            return labeled, "labeled_text"
        candidates = [
            ("meta", meta),
            ("json_ld", json_ld),
            ("time_tag", time_tag),
            ("text", text),
            # dateUpdate 是“更新时间”，只能作为最后的兜底，不能冒充发布时间。
            ("meta", meta_update),
        ]
        for source, value in candidates:
            if value:
                return value, source
        return "", ""

    def _extract_date_value(self, text: str) -> str:
        """Return the first valid date substring from text, or an empty string."""
        # XPath/meta 可能给出带时区的 ISO 时间（2026-11-10T23:30:00Z）。
        # 先完整保留，交给 parse_datetime 按真实偏移解析；若只截日期会丢失时刻和时区。
        for iso in _ISO_DATETIME_RE.finditer(text):
            candidate = iso.group(1)
            normalized = candidate
            if normalized.endswith(("Z", "z")):
                normalized = normalized[:-1] + "+00:00"
            normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
            try:
                dt.datetime.fromisoformat(normalized)
            except ValueError:
                continue
            return candidate
        for pattern in self.time_pattern:
            match = pattern.search(text)
            if match:
                result = match.group(1)
                if self._is_valid_date(result):
                    # 相邻文本节点可能把“18-03-08”和“23:16”直接拼在一起，
                    # 统一走同一套空白修复，避免把粘连值写进基线或数据库。
                    return self._normalize_time_text(result)
        return ""

    def _date_key(self, value: str) -> tuple[int | None, int, int] | None:
        match = re.search(
            r"(?:(?P<year>20\d{2})[-/年.])?(?P<month>\d{1,2})[-/月.](?P<day>\d{1,2})",
            value,
        )
        if not match:
            return None
        year = int(match.group("year")) if match.group("year") else None
        return year, int(match.group("month")), int(match.group("day"))

    @staticmethod
    def _has_time(value: str) -> bool:
        return bool(re.search(r"\d{1,2}[:：]\d{2}", value))

    def _same_date(self, left: str, right: str) -> bool:
        left_key = self._date_key(left)
        right_key = self._date_key(right)
        if left_key is None or right_key is None:
            return False
        left_year, left_month, left_day = left_key
        right_year, right_month, right_day = right_key
        if (left_month, left_day) != (right_month, right_day):
            return False
        return left_year is None or right_year is None or left_year == right_year

    def extract_from_user_xpath(self, publish_time_xpath: str, element: HtmlElement) -> str:
        if not publish_time_xpath:
            return ""
        try:
            values = element.xpath(publish_time_xpath)
        except Exception:  # noqa: BLE001 - 用户配置错误不应中断整篇抽取
            return ""
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not values:
            return ""
        for value in values:
            if isinstance(value, str):
                text = value
            elif isinstance(getattr(value, "tag", None), str):
                # lxml.html 元素有 text_content()；纯 etree 元素没有，用 itertext 兜底，
                # 避免配置写成元素路径时被静默跳过。
                text_content = getattr(value, "text_content", None)
                if callable(text_content):
                    text = text_content()
                else:
                    text = "".join(value.itertext())
            else:
                # count()/boolean() 等标量结果没有日期语义，直接跳过。
                continue
            result = self._extract_date_value(text)
            if result:
                return result
        return ""

    @staticmethod
    def _is_valid_date(date_str: str) -> bool:
        """Check that month is 1-12 and day is 1-31."""
        date_part = date_str.strip().split()[0]
        date_part = date_part.replace("年", "-").replace("月", "-").replace("日", "")
        parts = _DATE_SEP_RE.split(date_part)
        try:
            if len(parts) >= 2:
                month = int(parts[-2])
                day = int(parts[-1])
                if month < 1 or month > 12:
                    return False
                if day < 1 or day > 31:
                    return False
        except (ValueError, IndexError):
            pass
        return True

    def extract_from_json_ld(self, element: HtmlElement) -> str:
        for script_text in element.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                data = json.loads(script_text.strip())
                if isinstance(data, list):
                    data = data[0]
                if not isinstance(data, dict):
                    continue
                for key in ("datePublished", "dateCreated"):
                    date_val = data.get(key, "")
                    if date_val:
                        return str(date_val).strip()
            except (json.JSONDecodeError, IndexError, TypeError):
                continue
        return ""

    def extract_from_time_tag(self, element: HtmlElement) -> str:
        for attr in ("datetime", "data-published", "data-timestamp"):
            time_values = element.xpath(f"//time[@{attr}]/@{attr}")
            if time_values:
                return time_values[0].strip()
        return ""

    @staticmethod
    def _is_block(element: HtmlElement) -> bool:
        tag = getattr(element, "tag", "")
        return isinstance(tag, str) and tag.lower() in _BLOCK_TAGS

    @staticmethod
    def _is_skipped_element(element: HtmlElement | None) -> bool:
        current = element
        while current is not None:
            tag = getattr(current, "tag", "")
            if isinstance(tag, str):
                lowered = tag.lower()
                if lowered in _SKIP_TAGS:
                    return True
                # aside 可能是文章头部元信息（如 gamersky），只跳过带
                # 侧栏/相关推荐类标记的 aside，不能一刀切。
                if lowered == "aside":
                    attrs = " ".join(
                        value for value in (current.get("class"), current.get("id"))
                        if value
                    ).lower()
                    if any(hint.strip() in attrs for hint in _SIDEBAR_HINTS):
                        return True
            current = current.getparent()
        return False

    def _is_skipped_text(self, node) -> bool:
        parent = node.getparent()
        # tail 文本属于前一个元素的父元素，不随该元素一起被跳过：
        # <div>发布时间：<script>...</script>2026-11-05</div> 里的日期是
        # script.tail，仍然是页面可见文本。
        if getattr(node, "is_tail", False):
            parent = parent.getparent() if parent is not None else None
        return self._is_skipped_element(parent)

    def _text_nodes(self, element: HtmlElement):
        for node in element.xpath(".//text()"):
            if self._is_skipped_text(node):
                continue
            if str(node).strip():
                yield node

    def _text_groups(self, element: HtmlElement) -> list[str]:
        """按最近的块级祖先聚合文本，支持跨行内节点拼接日期。

        只在同一块内拼接，避免把相邻段落的年份和日期拼成伪日期；
        形如 2019<span>-09-30 22:46:13</span> 的拆分仍能还原。
        """
        groups: dict[int, list[str]] = {}
        order: list[int] = []
        for node in self._text_nodes(element):
            block = node.getparent()
            while block is not None and not self._is_block(block):
                block = block.getparent()
            key = id(block if block is not None else element)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(str(node))
        return ["".join(groups[key]) for key in order]

    def _iter_following_text(self, node):
        """按 DOM 顺序产出标签文本节点之后的文本，遇到块级边界停止。"""
        parent = node.getparent()
        if parent is None:
            return
        nodes = [
            item
            for item in parent.xpath(".//text()")
            if not self._is_skipped_text(item)
        ]
        start = 0
        for index, item in enumerate(nodes):
            if item is node or (
                str(item) == str(node)
                and item.getparent() is parent
                and bool(getattr(item, "is_tail", False))
                == bool(getattr(node, "is_tail", False))
            ):
                start = index + 1
                break
        for item in nodes[start:]:
            owner = item.getparent()
            if owner is not None and owner is not parent and self._is_block(owner):
                break
            yield str(item)
        # 标签和日期可能分处相邻的行内元素：
        # <div><span>发布时间：</span><span>2026-11-11</span></div>。
        # parent.xpath(".//text()") 只能看到标签自身，必须继续读后续兄弟节点，
        # 直到下一个块级元素；否则会退到全文扫描并命中正文里的旧日期。
        sibling = parent.getnext()
        while sibling is not None:
            if self._is_block(sibling):
                break
            if not self._is_skipped_element(sibling):
                # 必须复用 _text_nodes：脚本/样式里的日期是变量，不是页面可见文本。
                for text in self._text_nodes(sibling):
                    yield str(text)
            if sibling.tail:
                yield str(sibling.tail)
            sibling = sibling.getnext()
        # 标签所在元素可能只是行内 span，日期位于 span.tail，而不是任何
        # 子元素的 .text 中；向上逐级取 tail，遇到块级祖先即停。
        current = parent
        while current is not None and not self._is_block(current):
            if self._is_skipped_element(current):
                current = current.getparent()
                continue
            tail = getattr(current, "tail", None)
            if tail:
                yield str(tail)
            current = current.getparent()

    def _labeled_window(self, node) -> str:
        """取标签文本节点之后的有限窗口，不跨块级元素。"""
        parts = [str(node)]
        for text in self._iter_following_text(node):
            parts.append(text)
            window = "".join(parts)
            if self._extract_date_value(window):
                return window
        return "".join(parts)

    @staticmethod
    def _trim_labeled_text(value: str) -> str:
        stop = _LABELED_STOP_WORDS.search(value)
        if stop is not None:
            return value[: stop.start()]
        return value

    @staticmethod
    def _normalize_time_text(value: str) -> str:
        """压缩空白并修复文本节点直接拼接造成的日期时间粘连。"""
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(
            r"(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,2})(\d{1,2}:\d{2}(?::\d{2})?)",
            r"\1 \2",
            value,
        )
        return re.sub(
            r"(\d{4}年\d{1,2}月\d{1,2}日)(\d{1,2}:\d{2}(?::\d{2})?)",
            r"\1 \2",
            value,
        )

    def extract_from_labeled_text(self, element: HtmlElement) -> str:
        """优先读取“发布时间：2026年11月11日”这类带标签的日期。

        标签与日期可能被拆成同一段落里的两个文本节点，例如
        ``<p>发布时间：<span>2026-11-05</span></p>``；也可能分属相邻
        ``span``。只从标签节点向后看有限窗口，且不跨块级元素，避免把
        正文段落里的日期误当发布时间。
        """
        # 发布时间优先于更新时间；只有没有发布时间时才用更新时间。
        for labels in (("发布时间", "发布日期", "发布于"), ("更新时间",)):
            for node in self._text_nodes(element):
                text = str(node)
                if not any(label in text for label in labels):
                    continue
                window = self._labeled_window(node)
                for labeled in _LABELED_DATE_RE.finditer(window):
                    date_text = self._trim_labeled_text(labeled.group(1))
                    result = self._extract_date_value(date_text)
                    if result:
                        return result
        return ""

    def extract_from_text(self, element: HtmlElement) -> str:
        # 按模式优先级扫描：先找“完整日期时间”，再退到“月日”等弱格式；
        # 同一模式内按 DOM 文本节点顺序取第一个。
        # script/style/noscript/footer/nav/aside 不是正文展示内容，必须排除。
        text_nodes = [str(node) for node in self._text_nodes(element)]
        text_groups = self._text_groups(element)
        # 同一优先级先按 DOM 顺序找单节点，再查块内拼接文本：
        # 日期可能被 <span> 拆成多个节点，不能让“月日”这类弱格式
        # 在完整日期之前抢先命中；只在同一块级祖先内拼接，避免跨段落伪日期。
        for pattern in self.time_pattern:
            for text in text_nodes:
                if not text.strip():
                    continue
                match = pattern.search(text)
                if match:
                    result = match.group(1)
                    if self._is_valid_date(result):
                        return self._normalize_time_text(result)
            for text in text_groups:
                match = pattern.search(text)
                if match:
                    result = match.group(1)
                    if self._is_valid_date(result):
                        return self._normalize_time_text(result)
        return ""

    def extract_from_meta(
        self,
        element: HtmlElement,
        *,
        include_update: bool = True,
        only_update: bool = False,
    ) -> str:
        """Read publish time from known meta tags.

        include_update=False 用于排除 dateUpdate/dateModified（更新时间不能
        冒充发布时间）；only_update=True 只取更新时间，作为最后兜底。
        """
        for xpath in PUBLISH_TIME_META:
            is_update = "dateUpdate" in xpath or "dateModified" in xpath
            if only_update and not is_update:
                continue
            if not include_update and is_update:
                continue
            publish_time = element.xpath(xpath)
            if publish_time:
                return self._normalize_meta_datetime("".join(publish_time))
        return ""

    @staticmethod
    def _normalize_meta_datetime(value: str) -> str:
        """把 2007-08-20-23:59:59 这类 meta 值规范成空格分隔。"""
        value = value.strip()
        return re.sub(
            r"^(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})-(\d{1,2}:\d{2})",
            r"\1 \2",
            value,
        )

    # 保留旧方法名，兼容已有调用方。
    extractor = extract
