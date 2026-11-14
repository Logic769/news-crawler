"""清洗 GNE 抽出的正文 HTML，供前端安全渲染。

GNE 的 body_html 是原文正文容器的序列化结果，含站点自带的 class/style、
脚本、内联事件与相对图片地址。直接把这段 HTML 交给 v-html 等于把第三方
页面当代码执行，因此入库前统一走这里：

- 只保留语义化排版标签（段落/标题/列表/表格/图片/链接/代码块）；
- 删除 script/style/iframe/form 等可执行或可交互节点（连同内容）；
- 属性白名单：只留 href/title/src/alt/colspan/rowspan 等展示必需项，
  所有 on* 事件、style、id、class 一律丢掉；
- 相对 URL 按文章地址补全，只放行 http/https；
- 输出仍是 HTML 片段（不含外层 div），空正文返回空串。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

# 允许保留的排版标签。div/section 虽然语义弱，但正文容器里常用它们分组，
# 删掉会丢段落结构；样式由前端 scoped CSS 统一接管。
_ALLOWED_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "blockquote", "br",
    "caption", "code", "col", "colgroup", "dd", "del", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "i", "img", "ins", "li", "mark", "ol", "p", "pre", "q", "s", "section",
    "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "u", "ul",
}

# 连同子树一起删除的节点：留在页面上要么能执行脚本，要么会加载外部资源、
# 提交表单。noscript/template 里的内容对正文没有价值，一并删除。
_DROP_TAGS = {
    "applet", "audio", "base", "button", "canvas", "embed", "form", "frame",
    "frameset", "head", "iframe", "input", "link", "meta", "noscript",
    "object", "option", "script", "select", "style", "template", "textarea",
    "video",
}

_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
}

_URL_ATTRS = {"href", "src"}
_SAFE_SCHEMES = {"http", "https"}
_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


def _safe_url(value: str, base_url: str) -> str:
    """Return an absolute http(s) URL, or '' when the scheme is not allowed."""
    raw = (value or "").strip()
    if not raw:
        return ""
    # 先去掉浏览器会忽略、但会让 urlsplit 误判 scheme 的控制字符（\x00-\x1f）。
    raw = "".join(ch for ch in raw if ord(ch) >= 0x20)
    if not raw:
        return ""
    absolute = urljoin(base_url, raw) if base_url else raw
    try:
        scheme = urlsplit(absolute).scheme.lower()
    except ValueError:
        return ""
    if scheme not in _SAFE_SCHEMES:
        return ""
    return absolute


def _clean_element(element: etree._Element, base_url: str) -> None:
    tag = str(element.tag).lower() if isinstance(element.tag, str) else ""
    if not tag:
        return
    if tag not in _ALLOWED_TAGS:
        # 非白名单但结构无害的标签（font/center 等）去掉标签保留文字；
        # 危险标签已在前面整棵删除。
        element.drop_tag()
        return
    allowed = _ALLOWED_ATTRS.get(tag, set())
    for name in list(element.attrib):
        lowered = name.lower()
        if lowered not in allowed:
            del element.attrib[name]
            continue
        if lowered in _URL_ATTRS:
            cleaned = _safe_url(element.attrib[name], base_url)
            if cleaned:
                element.attrib[name] = cleaned
            else:
                del element.attrib[name]
        else:
            value = _WHITESPACE_RE.sub(" ", element.attrib[name]).strip()
            if value:
                element.attrib[name] = value
            else:
                del element.attrib[name]
    if tag == "img" and "src" not in element.attrib:
        # 没有可用地址的图片节点没有展示意义，留着只会出现破图占位。
        element.drop_tag()


def sanitize_body_html(raw_html: str | None, base_url: str = "") -> str:
    """Return a safe HTML fragment for the article body (may be empty)."""
    raw = (raw_html or "").strip()
    if not raw:
        return ""
    try:
        container = lxml_html.fragment_fromstring(raw, create_parent="div")
    except (etree.ParserError, ValueError):
        return ""

    # 深度优先遍历会受 drop_tree 影响，先收集快照再逐个处理。
    for element in list(container.iter()):
        if element is container:
            continue
        if not isinstance(element.tag, str):
            # 注释 / processing instruction：整条删除，避免把注释里的条件注释
            # （<!--[if IE]>...）带进前端。
            element.drop_tree() if element.getparent() is not None else None
            continue
        tag = element.tag.lower()
        if tag in _DROP_TAGS:
            parent = element.getparent()
            if parent is not None:
                element.drop_tree()
            continue
        _clean_element(element, base_url)

    parts: list[str] = []
    if container.text and container.text.strip():
        parts.append(container.text)
    for child in container:
        parts.append(etree.tostring(child, encoding="unicode", method="html"))
    cleaned = "".join(parts).strip()
    # 只有空段落/换行的正文等同没有 HTML，交给前端回退到纯文本渲染。
    if not cleaned or not re.sub(r"<[^>]+>|\s|&nbsp;", "", cleaned):
        return ""
    return cleaned
