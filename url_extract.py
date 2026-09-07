"""Fetch a news article URL and extract title/content with GNE."""

import json
import re
import ssl
import sys
import urllib.error
import urllib.request

from gne import GeneralNewsExtractor, NoContentException

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
}


def content_needs_render(content: str) -> bool:
    """Return True when body looks empty or is only a JS-loading placeholder."""
    text = (content or "").strip()
    if not text:
        return True
    placeholders = ("正在加载", "加载中", "内容加载中", "请稍候")
    return len(text) < 500 and any(p in text for p in placeholders)


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
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)
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


def fetch_html(url: str) -> str:
    """Fetch a page and decode it using the charset from headers/meta tags."""

    def _fetch(context: ssl.SSLContext) -> bytes:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25, context=context) as resp:
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


def extract_article(url: str, extractor: GeneralNewsExtractor) -> dict | None:
    print("正在抓取:", url)
    try:
        html = fetch_html(url)
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403, 406, 429):
            print(f"抓取失败（HTTP {exc.code}）。")
            return None
        print(f"直接抓取被拒绝(HTTP {exc.code})，正在用本机 Chrome 无头模式渲染...")
        html = fetch_html_with_browser(url)
        if html is None:
            print("浏览器回退失败。")
            return None
    except (urllib.error.URLError, ValueError, TimeoutError, ConnectionError) as exc:
        print(f"抓取失败（网络错误或网址无效）：{exc}")
        return None

    try:
        result = extractor.extract(html)
    except NoContentException:
        result = None

    if result is None or content_needs_render(result.get("content")):
        print("直接抓取未提取到正文，正在用本机 Chrome 无头模式渲染...")
        html = fetch_html_with_browser(url)
        if html is None:
            print("无法提取正文。可能是列表页，或不是新闻文章页。")
            return None
        try:
            result = extractor.extract(html)
        except NoContentException:
            result = None
        if result is None or content_needs_render(result.get("content")):
            print("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
            return None
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

