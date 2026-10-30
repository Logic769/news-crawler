"""Fetch a news article URL and extract title/content with GNE."""

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from html import unescape

from gne import GeneralNewsExtractor, NoContentException

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
}


MIN_CONTENT_LENGTH = 100


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target before urllib follows it."""

    def __init__(self, validator: Callable[[str], None]) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def content_is_too_short(content: str) -> bool:
    """Return True when the body is shorter than the useful minimum."""
    return len((content or "").strip()) < MIN_CONTENT_LENGTH


def content_needs_render(content: str) -> bool:
    """Return True when body is empty, too short, or a JS-loading placeholder."""
    text = (content or "").strip()
    if not text:
        return True
    if content_is_too_short(text):
        return True
    placeholders = ("正在加载", "加载中", "内容加载中", "请稍候")
    return len(text) < 500 and any(p in text for p in placeholders)


_VIDEO_PATH_RE = re.compile(r"/(?:shipin|video|vod|videos)/", re.IGNORECASE)


def _json_ld_type_sets(html_text: str) -> tuple[set[str], set[str]]:
    """Return (main entity types, all types) from JSON-LD blocks.

    Main entity = top-level object, first element of a top-level array, or first
    typed item inside @graph. This avoids treating an article page as video
    just because its @graph embeds a related VideoObject.
    """
    main_types: set[str] = set()
    all_types: set[str] = set()

    def add_types(value, target: set[str]) -> None:
        if isinstance(value, str):
            target.add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    target.add(item)

    def walk(value) -> None:
        if isinstance(value, dict):
            add_types(value.get("@type"), all_types)
            for key, child in value.items():
                if key != "@type" and isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for match in re.finditer(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        (html_text or "")[:60000],
    ):
        try:
            data = json.loads(match.group(1).strip())
        except Exception:
            continue
        walk(data)

        main = data
        if isinstance(data, list):
            main = next((item for item in data if isinstance(item, dict)), None)
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            main = next(
                (item for item in data["@graph"] if isinstance(item, dict) and item.get("@type")),
                None,
            )
        if isinstance(main, dict):
            add_types(main.get("@type"), main_types)

    return main_types, all_types


def is_video_page(url: str, html_text: str) -> bool:
    """明确的视频页信号才返回 True，避免把内嵌播放器的普通文章误判为视频页。"""
    from urllib.parse import urlparse

    path = urlparse(url or "").path or ""
    if _VIDEO_PATH_RE.search(path):
        return True

    head = (html_text or "")[:60000]
    for match in re.finditer(r"(?is)<meta\b[^>]*>", head):
        tag = match.group(0)
        if re.search(r'(?is)(?:property|name)\s*=\s*["\']og:type["\']', tag) and re.search(
            r'(?is)content\s*=\s*["\']video', tag
        ):
            return True
    main_types, all_types = _json_ld_type_sets(html_text)
    if {"VideoObject", "Video"} & main_types:
        return True
    # 普通文章的 @graph 可能内嵌相关视频；有 Article 类型时不以视频页处理
    if ({"VideoObject", "Video"} & all_types) and not ({"Article", "NewsArticle"} & all_types):
        return True
    return False


def _meta_map(html_text: str) -> dict:
    metas = {}
    for match in re.finditer(r"(?is)<meta\b[^>]*>", html_text or ""):
        tag = match.group(0)
        km = re.search(r'(?is)\b(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag)
        cm = re.search(r'(?is)\bcontent\s*=\s*["\'](.*?)["\']', tag)
        if km and cm:
            key = km.group(1).strip().lower()
            if key not in metas:
                metas[key] = unescape(cm.group(1)).strip()
    return metas


def _json_ld_published(html_text: str) -> str:
    for match in re.finditer(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text or "",
    ):
        dm = re.search(r'(?is)"datePublished"\s*:\s*"([^"]+)"', match.group(1))
        if dm:
            return dm.group(1).strip()
    return ""


def _date_from_url(url: str) -> str:
    match = re.search(r"/(20\d{2})[-/]?(\d{1,2})[-/]?(\d{1,2})/", url or "")
    if match:
        return "%s-%02d-%02d" % (match.group(1), int(match.group(2)), int(match.group(3)))
    return ""


def extract_video_meta(html_text: str, url: str) -> dict | None:
    """Extract title/summary/publish time for video pages from meta/JSON-LD/URL."""
    metas = _meta_map(html_text)
    title = metas.get("og:title", "")
    if not title:
        h1 = re.search(r"(?is)<h1\b[^>]*>(.*?)</h1>", html_text or "")
        if h1:
            title = unescape(re.sub(r"<[^>]+>", "", h1.group(1))).strip()
    if not title:
        t = re.search(r"(?is)<title>(.*?)</title>", html_text or "")
        if t:
            title = unescape(t.group(1)).strip()
    if title:
        # 仅剥离形如“-中新网视频”的站点后缀，不切分 COVID-19 这类普通连字符
        title = re.sub(
            r"\s*[-–—－|_]\s*[^-–—－|_]{1,20}(?:网|视频|新闻|频道|首页|网站|客户端)$",
            "",
            title,
        ).strip()

    description = metas.get("og:description") or metas.get("description") or ""
    publish = (
        metas.get("article:published_time")
        or metas.get("og:published_time")
        or metas.get("publish_date")
        or _json_ld_published(html_text)
        or metas.get("publishdate")
        or metas.get("pubdate")
        or _date_from_url(url)
        or metas.get("date")
        or ""
    )
    if re.match(r"^\d{4}-\d{2}-\d{2}T", publish):
        publish = publish[:10]
    author = metas.get("author") or ""
    image = metas.get("og:image") or ""
    content = description or title
    if not title and not description:
        return None
    return {
        "title": title,
        "author": author,
        "publish_time": publish,
        "content": content,
        "images": [image] if image else [],
        "is_video": True,
    }


def fetch_html_with_browser(url: str) -> str | None:
    """Render a page with headless Chrome (Playwright) as a fallback."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 Playwright，无法使用浏览器回退。可运行: pip install playwright")
        return None

    try:
        with sync_playwright() as p:
            browser = None
            launch_args = ["--disable-dev-shm-usage"]
            try:
                if os.geteuid() == 0:
                    launch_args.insert(0, "--no-sandbox")
            except AttributeError:
                pass
            try:
                browser = p.chromium.launch(channel="chrome", headless=True, args=launch_args)
            except Exception:
                browser = p.chromium.launch(headless=True, args=launch_args)
            try:
                page = browser.new_page(
                    user_agent=HEADERS["User-Agent"],
                    locale="zh-CN",
                )
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                except Exception:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:
        print("浏览器回退失败:", exc)
        return None


def fetch_html(url: str, *, url_validator: Callable[[str], None] | None = None) -> str:
    """Fetch a page and decode it using the charset from headers/meta tags.

    url_validator, when given, is called again for every redirect target so a
    caller-side allow-list cannot be bypassed by a 3xx response.
    """

    def _fetch(context: ssl.SSLContext) -> bytes:
        handlers: list[urllib.request.BaseHandler] = []
        if url_validator is not None:
            handlers.append(_ValidatingRedirectHandler(url_validator))
        handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers=HEADERS)
        with opener.open(req, timeout=25) as resp:
            return resp.read(), resp.headers.get_content_charset()

    try:
        raw, charset = _fetch(ssl.create_default_context())
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        # Some government/news sites use non-standard certificate chains.
        raw, charset = _fetch(ssl._create_unverified_context())

    if not charset:
        head = raw[:4096].decode("ascii", "ignore")
        match = re.search(r'charset\s*=\s*["\']?([\w-]+)', head, re.IGNORECASE)
        charset = match.group(1) if match else None

    for encoding in (charset, "utf-8", "gb18030"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def extract_article(
    url: str,
    extractor: GeneralNewsExtractor,
    *,
    allow_browser: bool = True,
    url_validator: Callable[[str], None] | None = None,
) -> dict | None:
    print("正在抓取:", url)
    try:
        html = fetch_html(url, url_validator=url_validator)
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403, 406, 429):
            print(f"抓取失败（HTTP {exc.code}）。")
            return None
        if not allow_browser:
            print(f"直接抓取被拒绝(HTTP {exc.code})，且浏览器回退已禁用。")
            return None
        print(f"直接抓取被拒绝(HTTP {exc.code})，正在用本机 Chrome 无头模式渲染...")
        html = fetch_html_with_browser(url)
        if html is None:
            print("浏览器回退失败。")
            return None
    except (urllib.error.URLError, ValueError, TimeoutError, ConnectionError) as exc:
        print(f"抓取失败（网络错误或网址无效）：{exc}")
        return None

    if is_video_page(url, html):
        video = extract_video_meta(html, url)
        if video is not None:
            print("识别为视频页，使用 meta 适配抽取。")
            return video

    try:
        result = extractor.extract(html)
    except NoContentException:
        result = None

    if result is None or content_needs_render(result.get("content")):
        if not allow_browser:
            print("直接抓取未提取到正文，且浏览器回退已禁用。")
            return None
        print("直接抓取未提取到正文，正在用本机 Chrome 无头模式渲染...")
        html = fetch_html_with_browser(url)
        if html is None:
            print("无法提取正文。可能是列表页，或不是新闻文章页。")
            return None
        try:
            result = extractor.extract(html)
        except NoContentException:
            result = None
        if result is None:
            print("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
            return None
        if content_needs_render(result.get("content")):
            if not content_is_too_short(result.get("content")):
                print("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
                return None
            print(f"警告：渲染回退后正文仍不足 {MIN_CONTENT_LENGTH} 字，仍按原文返回。")
    return result


def main() -> None:
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("请输入新闻文章网址: ").strip()

    if not url:
        print("未输入网址。")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = extract_article(url, GeneralNewsExtractor())
    if result is None:
        return

    output = {
        "url": url,
        "title": result.get("title", ""),
        "publish_time": result.get("publish_time", ""),
        "author": result.get("author", ""),
        "content": result.get("content", ""),
        "images": result.get("images", []),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

