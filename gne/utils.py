"""Compatibility re-exports for the historical gne.utils module.

New code should import from gne.html, gne.text or gne.config directly.
"""

from .config import config, read_config
from .constants import (
    HIGH_WEIGHT_ARRT_KEYWORD,
    TAGS_CAN_BE_REMOVE_IF_EMPTY,
    USELESS_ATTR,
    USELESS_TAG,
)
from .html import (
    HIGH_WEIGHT_KEYWORD_RE,
    drop_tag,
    fix_html,
    get_high_weight_keyword_pattern,
    html2element,
    is_empty_element,
    iter_node,
    normalize_node,
    pad_host_for_images,
    pre_parse,
    remove_node,
    remove_noise_node,
)
from .text import get_longest_common_sub_string, normalize_text

__all__ = [
    "HIGH_WEIGHT_ARRT_KEYWORD",
    "HIGH_WEIGHT_KEYWORD_RE",
    "TAGS_CAN_BE_REMOVE_IF_EMPTY",
    "USELESS_ATTR",
    "USELESS_TAG",
    "config",
    "drop_tag",
    "fix_html",
    "get_high_weight_keyword_pattern",
    "get_longest_common_sub_string",
    "html2element",
    "is_empty_element",
    "iter_node",
    "normalize_node",
    "normalize_text",
    "pad_host_for_images",
    "pre_parse",
    "read_config",
    "remove_node",
    "remove_noise_node",
]
