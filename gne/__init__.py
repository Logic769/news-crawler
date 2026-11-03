"""News extraction engine public API."""

from . import defaults, extractor, utils
from .config import config
from .engine import GeneralNewsExtractor
from .exceptions import NoContentException
from .extractors import (
    AuthorExtractor,
    ContentExtractor,
    ListExtractor,
    ListPageExtractor,
    MetaExtractor,
    TimeExtractor,
    TitleExtractor,
)
from .html import fix_html, html2element, pre_parse, remove_noise_node
from .text import normalize_text

__all__ = [
    "AuthorExtractor",
    "ContentExtractor",
    "GeneralNewsExtractor",
    "ListExtractor",
    "ListPageExtractor",
    "MetaExtractor",
    "NoContentException",
    "TimeExtractor",
    "TitleExtractor",
    "config",
    "defaults",
    "extractor",
    "fix_html",
    "html2element",
    "normalize_text",
    "pre_parse",
    "remove_noise_node",
    "utils",
]
__version__ = "1.1.2"
__author__ = "Kingname"
