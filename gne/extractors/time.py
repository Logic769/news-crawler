"""Publish-time extraction from XPath, meta, JSON-LD, time tags and text."""

from __future__ import annotations

import json
import re

from lxml.html import HtmlElement

from ..config import config
from ..constants import DATETIME_PATTERN, PUBLISH_TIME_META

_DATE_SEP_RE = re.compile(r"[-/.]")


class TimeExtractor:
    """Extract an article publish time with the legacy priority order."""

    def __init__(self) -> None:
        self.time_pattern = DATETIME_PATTERN

    def extract(self, element: HtmlElement, publish_time_xpath: str = "") -> str:
        publish_time_xpath = publish_time_xpath or config.get("publish_time", {}).get("xpath")
        return (
            self.extract_from_user_xpath(publish_time_xpath, element)
            or self.extract_from_meta(element)
            or self.extract_from_json_ld(element)
            or self.extract_from_time_tag(element)
            or self.extract_from_text(element)
        )

    def extract_from_user_xpath(self, publish_time_xpath: str, element: HtmlElement) -> str:
        if publish_time_xpath:
            return "".join(element.xpath(publish_time_xpath))
        return ""

    @staticmethod
    def _is_valid_date(date_str: str) -> bool:
        """Check that month is 1-12 and day is 1-31."""
        date_part = date_str.strip().split()[0]
        date_part = date_part.replace("年", "-").replace("月", "-").replace("日", "")
        parts = _DATE_SEP_RE.split(date_part)
        try:
            if len(parts) >= 3:
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

    def extract_from_text(self, element: HtmlElement) -> str:
        text = "".join(element.xpath(".//text()"))
        for pattern in self.time_pattern:
            match = pattern.search(text)
            if match:
                result = match.group(1)
                if self._is_valid_date(result):
                    return result
        return ""

    def extract_from_meta(self, element: HtmlElement) -> str:
        """Read publish time from known meta tags."""
        for xpath in PUBLISH_TIME_META:
            publish_time = element.xpath(xpath)
            if publish_time:
                return "".join(publish_time)
        return ""

    # 保留旧方法名，兼容已有调用方。
    extractor = extract
