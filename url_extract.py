"""Fetch a news article URL and extract title/content with GNE."""

import http.client
import json
import logging
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from gne import GeneralNewsExtractor, NoContentException

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    # 新闻页普遍开启压缩，实测传输量降到 25%~66%；解压后才是原始 HTML。
    "Accept-Encoding": "gzip",
}


LOG = logging.getLogger("url_extract")

MIN_CONTENT_LENGTH = 100

# 证书校验失败时的降级开关。默认关闭：曾出现过“遇到证书链不规范就静默换成
# 不校验上下文重试”，等于对这类站点主动放弃 MITM 防护且不留痕。需要抓取
# 政府站等自签/链异常站点时，显式设置 NEWS_INSECURE_SSL=1，并接受日志里的
# WARNING。
INSECURE_SSL_ENV = "NEWS_INSECURE_SSL"
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

# 直接抓取被“拒绝”时浏览器才有意义（反爬/风控通常会放过真实浏览器）；
# 404/410/500 这类错误渲染后仍然是同样的错误，回退只会白等一次浏览器启动。
BROWSER_FALLBACK_HTTP_CODES = frozenset({401, 403, 406, 429})

# SSLContext 创建一次约 20ms，模块级复用避免每个请求都重新加载 CA 证书。
_SSL_CONTEXT = ssl.create_default_context()
_UNVERIFIED_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_CONTEXT_LOCK = threading.Lock()


def _insecure_ssl_allowed() -> bool:
    """是否允许证书校验失败后降级重试（默认不允许）。"""
    return os.environ.get(INSECURE_SSL_ENV, "").strip().lower() in _TRUE_ENV_VALUES


def _unverified_ssl_context() -> ssl.SSLContext:
    """惰性创建“不校验证书”的上下文，仅在 NEWS_INSECURE_SSL 显式开启时使用。"""
    global _UNVERIFIED_SSL_CONTEXT
    with _SSL_CONTEXT_LOCK:
        if _UNVERIFIED_SSL_CONTEXT is None:
            _UNVERIFIED_SSL_CONTEXT = ssl._create_unverified_context()
        return _UNVERIFIED_SSL_CONTEXT


def absolute_url(base: str, value: str) -> str:
    """把相对/协议相对地址补全为绝对 URL；无法解析时原样返回。"""
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return urljoin(base, text) if base else text


ALLOWED_IMAGE_SCHEMES = ("http://", "https://")


def normalize_images(images, base_url: str) -> list[str]:
    """把抽取结果里的相对图片地址补全为绝对 URL，并去掉空值。

    只保留 http/https：data:、javascript:、ftp: 等 scheme 不该出现在图片
    列表里，下游按 URL 直接取图时会失败或带来风险，这里一并过滤。
    """
    if not images:
        return []
    resolved: list[str] = []
    for item in images:
        url = absolute_url(base_url, str(item))
        if not url or not url.lower().startswith(ALLOWED_IMAGE_SCHEMES):
            continue
        if url not in resolved:
            resolved.append(url)
    return resolved


def normalize_images_for_update(images, base_url: str) -> list[str] | None:
    """归一化图片列表，空结果返回 None 表示"本次未提供"。

    GNE 抽不到图片不等于页面确实没有图片，调用方不应把空结果当成"明确
    无图"去清空历史值；save_article 只把显式的 [] 视为清空。
    """
    return normalize_images(images, base_url) or None


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target before urllib follows it."""

    def __init__(self, validator: Callable[[str], None]) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ip_literal_url(ip: str) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"https://{host}/"


def _validated_create_connection(
    host: str,
    port: int,
    timeout: float | None,
    url_validator: Callable[[str], None],
    *,
    ssl_context: ssl.SSLContext | None = None,
    server_hostname: str | None = None,
):
    """解析一次主机并在连接前校验「即将连接的那个 IP」。

    与「抓取前 getaddrinfo 校验一次」不同，这里是「解析 → 校验 → 用同一 sockaddr
    连接」原子完成，中间没有重新解析，从而消除 DNS rebinding（TOCTOU）绕过面。
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise urllib.error.URLError(f"无法解析主机 {host}: {exc}") from exc
    last_error: Exception | None = None
    for family, socktype, proto, _canon, sockaddr in infos:
        ip = str(sockaddr[0]).split("%", 1)[0]
        # 用 IP 字面量 URL 复用同一套地址策略校验该 IP 是否为公网地址。
        url_validator(_ip_literal_url(ip))
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            last_error = exc
            try:
                sock.close()
            except OSError:
                pass
            continue
        if ssl_context is not None:
            try:
                sock = ssl_context.wrap_socket(sock, server_hostname=server_hostname or host)
            except BaseException:
                try:
                    sock.close()
                except OSError:
                    pass
                raise
        return sock
    raise last_error or OSError(f"无法连接到 {host}")


# 注意：pinned 连接通过覆盖 connect() 直接连“DNS 解析后的真实 IP”，因此不再
# 经过 urllib 的 ProxyHandler。配置 HTTP(S)_PROXY 时，API extract 路径会直连而
# 不经过代理；这是防 DNS rebinding 的有意取舍（走代理时只能看到代理 IP，无法
# 用真实目标地址做校验）。crawler 不传 url_validator，不受此限制。
class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, url_validator: Callable[[str], None], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._url_validator = url_validator

    def connect(self) -> None:
        self.sock = _validated_create_connection(
            self.host, self.port, self.timeout, self._url_validator
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, url_validator: Callable[[str], None], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._url_validator = url_validator

    def connect(self) -> None:
        context = getattr(self, "_context", None)
        self.sock = _validated_create_connection(
            self.host,
            self.port,
            self.timeout,
            self._url_validator,
            ssl_context=context,
            server_hostname=self.host,
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, url_validator: Callable[[str], None]) -> None:
        super().__init__()
        self._url_validator = url_validator

    def http_open(self, req):  # noqa: ANN001
        return self.do_open(
            lambda host, **kw: _PinnedHTTPConnection(
                host, url_validator=self._url_validator, **kw
            ),
            req,
        )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, url_validator: Callable[[str], None], context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._url_validator = url_validator

    def https_open(self, req):  # noqa: ANN001
        return self.do_open(
            lambda host, **kw: _PinnedHTTPSConnection(
                host, url_validator=self._url_validator, **kw
            ),
            req,
            context=self._context,
        )


def content_is_too_short(content: str | None) -> bool:
    """Return True when the body is shorter than the useful minimum."""
    return len((content or "").strip()) < MIN_CONTENT_LENGTH


def content_needs_render(content: str | None) -> bool:
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
    """明确的视频页信号才返回 True；URL 路径只作提示，需配合页面内视频标记。"""
    from urllib.parse import urlparse

    path = urlparse(url or "").path or ""
    head = (html_text or "")[:60000]
    has_video_meta = False
    for match in _META_TAG_RE.finditer(head):
        tag = match.group(0)
        km = _META_KEY_RE.search(tag)
        cm = _META_CONTENT_RE.search(tag)
        if km and cm:
            key = (km.group(1) or km.group(2) or "").strip().lower()
            value = cm.group(1) if cm.group(1) is not None else cm.group(2)
            value = (value or "").strip()
            if key == "og:type" and value.lower().startswith("video"):
                return True
            if key in {"og:video", "og:video:url", "og:video:secure_url"} and value:
                has_video_meta = True
    main_types, all_types = _json_ld_type_sets(html_text)
    if {"VideoObject", "Video"} & main_types:
        return True
    # 普通文章的 @graph 可能内嵌相关视频；有 Article 类型时不以视频页处理
    if ({"VideoObject", "Video"} & all_types) and not ({"Article", "NewsArticle"} & all_types):
        return True
    # 路径命中 /video|shipin|vod/ 不再直接判为视频页，必须有 <video> 或 og:video
    # 这类页面内证据，否则 /video/ 下的图文稿件会被误判。
    has_video_tag = bool(re.search(r"(?is)<video(?=[ />])", head))
    # /video/ 路径只是提示；主实体是 NewsArticle 时，内嵌 <video> 不代表
    # 整页是视频页（与上面的 @graph 分支保持同一判断）。
    if (
        _VIDEO_PATH_RE.search(path)
        and (has_video_tag or has_video_meta)
        and not ({"Article", "NewsArticle"} & main_types)
    ):
        return True
    return False


_META_TAG_RE = re.compile(
    r'''(?is)<meta(?=[ />])(?:[^>"']|"[^"]*"|'[^']*')*>'''
)
_META_KEY_RE = re.compile(
    r'''(?is)(?:property|name)[ ]*=[ ]*(?:"([^"]+)"|'([^']+)')'''
)
_META_CONTENT_RE = re.compile(
    r'''(?is)content[ ]*=[ ]*(?:"([^"]*)"|'([^']*)')'''
)


def _meta_map(html_text: str) -> dict:
    metas = {}
    for match in _META_TAG_RE.finditer(html_text or ""):
        tag = match.group(0)
        km = _META_KEY_RE.search(tag)
        cm = _META_CONTENT_RE.search(tag)
        if km and cm:
            key = (km.group(1) or km.group(2) or "").strip().lower()
            value = cm.group(1) if cm.group(1) is not None else cm.group(2)
            if key and key not in metas:
                metas[key] = unescape(value or "").strip()
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
        return (
            f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        )
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
        "images": normalize_images([image], url) if image else [],
        "is_video": True,
    }


# 浏览器渲染的单页开销里，启动约占 1s、固定等待曾占 2.5s。这里把浏览器进程
# 在进程内复用，并把“死等 2.5 秒”改成「等 networkidle，最多再等 1.5 秒」，
# 页面已就绪时立即返回。sync_playwright 不可跨线程共享，因此按线程隔离。
_RENDER_LOCAL = threading.local()
RENDER_SETTLE_TIMEOUT_MS = 1500
# 每次导航（先 networkidle，失败再 domcontentloaded）最多 15s。旧值 60s 会让
# 单页最坏等满两次共 ~120s，一个坏站点就能把整轮抓取拖住；15s + 1.5s 收尾
# 把单页上限压到 ~31.5s，同时保留二次尝试的机会。
RENDER_NAV_TIMEOUT_MS = 15000


def _thread_browser() -> tuple[Any, Any]:
    """Return this thread's (playwright, browser), launching on demand."""
    state = getattr(_RENDER_LOCAL, "state", None)
    if state is not None:
        if _browser_alive():
            return state
        # 浏览器已断开：先清掉旧 state，否则重启后会留下孤儿 playwright 进程。
        close_thread_browser()
    from playwright.sync_api import sync_playwright

    launch_args = ["--disable-dev-shm-usage"]
    # os.geteuid 只在 POSIX 存在；容器里以 root 运行时 Chromium 必须加
    # --no-sandbox，Windows/macOS 本地开发则不需要。
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        launch_args.insert(0, "--no-sandbox")
    playwright = sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch(
                channel="chrome", headless=True, args=launch_args
            )
        except Exception:
            browser = playwright.chromium.launch(headless=True, args=launch_args)
    except BaseException:
        # 启动失败必须回收 driver，否则每失败一次就留下一个常驻 node 进程。
        try:
            playwright.stop()
        except Exception:
            pass
        raise
    _RENDER_LOCAL.state = (playwright, browser)
    return _RENDER_LOCAL.state


def _browser_alive() -> bool:
    """本线程的浏览器是否仍然可用；任何异常都视为已断开。"""
    state = getattr(_RENDER_LOCAL, "state", None)
    if state is None:
        return False
    try:
        return bool(state[1].is_connected())
    except Exception:
        return False


def close_thread_browser() -> None:
    """Release this thread's browser (called when a worker finishes)."""
    state = getattr(_RENDER_LOCAL, "state", None)
    if state is None:
        return
    _RENDER_LOCAL.state = None
    playwright, browser = state
    for closer in (browser.close, playwright.stop):
        try:
            closer()
        except Exception:
            pass


def fetch_html_with_browser(
    url: str, *, url_validator: Callable[[str], None] | None = None
) -> str | None:
    """Render a page with headless Chrome (Playwright) as a fallback.

    每个页面使用独立的 BrowserContext：context 之间 cookie/localStorage 相互
    隔离，避免同一线程连续抓多个站点时把 A 站的会话带到 B 站，进而触发反爬
    或拿到个性化内容。浏览器进程本身仍按线程复用。
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        LOG.warning("未安装 Playwright，无法使用浏览器回退。可运行: pip install playwright")
        return None

    if url_validator is not None:
        try:
            url_validator(url)
        except Exception as exc:
            LOG.warning("浏览器回退目标被拒绝：%s", exc)
            return None

    page = None
    context = None
    try:
        _playwright, _browser = _thread_browser()
        context = _browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
        )
        page = context.new_page()
        if url_validator is not None:
            # 浏览器自行导航、跟随重定向，绕过 fetch_html 的校验；在请求级别
            # 再校验所有 http(s) 请求（含图片/XHR/fetch），拦截 3xx/JS 跳转或
            # 页面子资源指向内网/云元数据地址造成的 SSRF。按 host 缓存校验结果，
            # 避免同一页面的大量子资源重复做 DNS 解析。
            _host_ok: dict[str, bool] = {}

            def _guard(route):  # noqa: ANN001
                request = route.request
                try:
                    parts = urlsplit(request.url)
                except ValueError:
                    route.abort()
                    return
                if parts.scheme not in ("http", "https"):
                    # data:/blob:/about: 等本地资源不会发起网络请求，直接放行。
                    route.continue_()
                    return
                host = (parts.hostname or "").lower()
                if not host:
                    route.abort()
                    return
                if host not in _host_ok:
                    try:
                        url_validator(request.url)
                    except Exception as exc:
                        LOG.warning(
                            "浏览器请求目标被拒绝（%s）：%s", exc, request.url
                        )
                        _host_ok[host] = False
                    else:
                        _host_ok[host] = True
                if not _host_ok[host]:
                    route.abort()
                    return
                route.continue_()

            # 用 context 而不是 page 注册：window.open 新开标签页的首个请求
            # 也必须经过同一套 URL/IP 校验，page.route 拦截不到弹窗页面。
            context.route("**/*", _guard)
        try:
            page.goto(url, wait_until="networkidle", timeout=RENDER_NAV_TIMEOUT_MS)
        except Exception:
            page.goto(url, wait_until="domcontentloaded", timeout=RENDER_NAV_TIMEOUT_MS)
        # 只给“正文容器出现”留很短的时间窗：页面已就绪时立即返回，
        # 未就绪时最多再等 RENDER_SETTLE_TIMEOUT_MS，避免每页固定空等。
        try:
            page.wait_for_load_state("networkidle", timeout=RENDER_SETTLE_TIMEOUT_MS)
        except Exception:
            pass
        return page.content()
    except Exception as exc:
        LOG.warning("浏览器回退失败：%s", exc)
        # 只有浏览器真的断开（进程崩溃/被系统杀掉）才丢掉线程状态；
        # 单页导航失败（超时、4xx、DNS 失败）不应销毁可复用的浏览器。
        # is_connected() 在 driver 已崩时自身会抛错，因此必须单独保护，
        # 否则异常会从 except 块逃出，把「回退失败」变成「抓取异常」。
        if not _browser_alive():
            close_thread_browser()
        return None
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


class UnsupportedContentEncoding(ValueError):
    """Response body is compressed with something we cannot decode."""


# 解压后的响应体上限：压缩炸弹（极小的 gzip 解出 GB 级内容）会让进程 OOM。
# 正常新闻页 HTML 远小于此，超限直接拒绝该响应。
MAX_DECODED_BYTES = 32 * 1024 * 1024

# 多 member gzip 每结束一段都要复制剩余压缩后缀并新建 decompressobj。恶意响应
# 可以用大量极小 member 在字节上限内放大 CPU/内存开销，因此额外限制 member 数量。
MAX_COMPRESSION_MEMBERS = 32

# urllib 的 timeout 只是单次 socket 操作超时，慢速滴流（每隔几十秒吐一个字节）
# 能让整个响应无限期拖住抓取线程。这里再加一个总墙钟预算，并配合按剩余预算
# 收缩 socket 超时来强制中止。
PER_OP_TIMEOUT = 25
MAX_FETCH_TOTAL_SECONDS = 30


def _check_size(data: bytes) -> bytes:
    if len(data) > MAX_DECODED_BYTES:
        raise UnsupportedContentEncoding(
            f"解压后超过 {MAX_DECODED_BYTES} 字节上限，拒绝该响应"
        )
    return data


def _decompress_stage(raw: bytes, wbits: int) -> bytes:
    """用 decompressobj 分块解压，边解边比对上限，超限立刻抛错。

    zlib.decompressobj.decompress(data, max_length) 单次最多输出 max_length 字节，
    剩余输入留在 unconsumed_tail，因此可以在解出超过上限的第一时间停下，不会像
    一次性 decompress 那样先把整颗压缩炸弹解进内存再判断大小。
    """
    obj = zlib.decompressobj(wbits)
    out = bytearray()
    buf = raw
    members = 1
    while True:
        out += obj.decompress(buf, MAX_DECODED_BYTES + 1 - len(out))
        if len(out) > MAX_DECODED_BYTES:
            raise UnsupportedContentEncoding(
                f"解压后超过 {MAX_DECODED_BYTES} 字节上限，拒绝该响应（疑似压缩炸弹）"
            )
        buf = obj.unconsumed_tail
        if buf:
            continue
        # 多 member（多个 gzip 流拼接）时，一个 member 结束后剩余字节在
        # unused_data 里；需用新的 decompressobj 继续解，否则第二段起被静默丢弃。
        # 仅 gzip 支持多 member：deflate 的尾随字节不是新 member（例如 smoke 里
        # 去掉 zlib 头的裸流会残留 Adler-32 尾字节），误当 member 会解压失败。
        if wbits > 15 and obj.eof and obj.unused_data:
            members += 1
            if members > MAX_COMPRESSION_MEMBERS:
                raise UnsupportedContentEncoding(
                    f"压缩流包含超过 {MAX_COMPRESSION_MEMBERS} 个 member，拒绝该响应"
                )
            buf = obj.unused_data
            obj = zlib.decompressobj(wbits)
            continue
        break
    out += obj.flush()
    if len(out) > MAX_DECODED_BYTES:
        raise UnsupportedContentEncoding(
            f"解压后超过 {MAX_DECODED_BYTES} 字节上限，拒绝该响应（疑似压缩炸弹）"
        )
    if not obj.eof:
        # 流未正常结束（如尾部 CRC/长度缺失），按“响应被截断”显式失败，
        # 不能把残缺解压结果当正文静默放行。
        raise UnsupportedContentEncoding("解压流不完整（响应可能被截断）")
    return bytes(out)


def _bounded_decompress(raw: bytes, coding: str) -> bytes:
    """按 coding 解压并限制输出大小；超限或数据损坏抛 UnsupportedContentEncoding。"""
    if coding == "gzip":
        wbits_options: tuple[int, ...] = (zlib.MAX_WBITS | 16,)
    else:
        # deflate：RFC 只要求 zlib 包装，但部分服务端发裸流（wbits=-15）。
        wbits_options = (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    last_error: Exception | None = None
    for wbits in wbits_options:
        try:
            return _decompress_stage(raw, wbits)
        except UnsupportedContentEncoding:
            raise
        except (zlib.error, EOFError) as exc:
            last_error = exc
            continue
    raise UnsupportedContentEncoding(
        f"{coding} 解压失败（响应可能被截断）: {last_error}"
    ) from last_error


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    """Decode a compressed response body.

    只请求了 gzip，正常情况下服务端也只会回 gzip。遇到 br 或多段编码时
    必须显式失败：静默透传会把压缩字节当成 HTML 解码，得到一堆乱码正文，
    最终被记成 no_content，线上几乎无法定位。
    """
    normalized = (encoding or "").strip().lower()
    if not normalized or normalized == "identity":
        return _check_size(raw)

    # Content-Encoding 可以叠加（"gzip, gzip"），解码顺序与编码顺序相反。
    codings = [part.strip() for part in normalized.split(",") if part.strip()]
    for coding in reversed(codings):
        if coding in ("gzip", "x-gzip"):
            raw = _bounded_decompress(raw, "gzip")
        elif coding == "deflate":
            raw = _bounded_decompress(raw, "deflate")
        elif coding == "identity":
            continue
        else:
            raise UnsupportedContentEncoding(
                f"不支持的内容编码 {coding}；请求只声明了 gzip"
            )
    return raw


def _response_socket(resp: Any) -> socket.socket | None:
    """尽力取出 HTTPResponse 底层的 socket，用于按剩余预算设置读取超时。

    取不到（实现差异/已被包装）时返回 None，调用方仍可依赖每轮墙钟检查兜底。
    """
    fp = getattr(resp, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    if isinstance(sock, socket.socket):
        return sock
    return None


def fetch_html(url: str, *, url_validator: Callable[[str], None] | None = None) -> str:
    """Fetch a page and decode it using the charset from headers/meta tags.

    url_validator, when given, is called again for every redirect target so a
    caller-side allow-list cannot be bypassed by a 3xx response.
    """

    def _fetch(context: ssl.SSLContext) -> tuple[bytes, str | None]:
        handlers: list[urllib.request.BaseHandler] = []
        if url_validator is not None:
            # 重定向目标先按 URL 策略校验（域名层），连接时再按真实解析出的
            # IP 校验（防 DNS rebinding）；两层都通过才建立连接。
            # build_opener 会自动补一个读取 HTTP(S)_PROXY 的 ProxyHandler；
            # API extract 的 url_validator 路径要求直连并校验真实解析 IP，
            # 否则会改连代理（校验代理地址或把 TLS 直接发给代理）。显式传空
            # 字典禁用环境代理；不带 validator 的爬虫路径保持原有代理行为。
            handlers.append(urllib.request.ProxyHandler({}))
            handlers.append(_ValidatingRedirectHandler(url_validator))
            handlers.append(_PinnedHTTPSHandler(url_validator, context))
            handlers.append(_PinnedHTTPHandler(url_validator))
        else:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers=HEADERS)
        # 总预算从建立连接前开始计时，连接、等待响应头、读取正文共用同一截止时间。
        deadline = time.monotonic() + MAX_FETCH_TOTAL_SECONDS
        open_timeout = min(PER_OP_TIMEOUT, deadline - time.monotonic())
        if open_timeout <= 0:
            raise TimeoutError(
                f"抓取超过 {MAX_FETCH_TOTAL_SECONDS} 秒总时限：{url}"
            )
        with opener.open(req, timeout=open_timeout) as resp:
            # 分块读取，边读边查总墙钟预算：单次 socket 超时挡不住慢速滴流，
            # 每轮把 socket 超时收缩到“剩余预算”，阻塞读取也会在剩余预算耗尽时
            # 由 socket.timeout 中止。同时累计字节数，未压缩但超大的响应不能
            # 无限读入内存；压缩流一般远小于解压结果，真正上限由 _decompress 兜底。
            sock = _response_socket(resp)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"抓取超过 {MAX_FETCH_TOTAL_SECONDS} 秒总时限：{url}"
                )
            if sock is not None:
                try:
                    sock.settimeout(min(PER_OP_TIMEOUT, remaining))
                except OSError:
                    pass
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"抓取超过 {MAX_FETCH_TOTAL_SECONDS} 秒总时限：{url}"
                    )
                if sock is not None:
                    try:
                        sock.settimeout(min(PER_OP_TIMEOUT, remaining))
                    except OSError:
                        pass
                try:
                    # read1 只做一次底层 recv 就返回，能让循环频繁回到墙钟检查；
                    # 若用 read(n) 会一直阻塞到读满 n 字节或 EOF，慢速滴流下
                    # 整个函数仍被拖到数据发完为止。
                    read_chunk = getattr(resp, "read1", None) or resp.read
                    chunk = read_chunk(65536)
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"抓取超过 {MAX_FETCH_TOTAL_SECONDS} 秒总时限：{url}"
                    ) from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DECODED_BYTES:
                    raise UnsupportedContentEncoding(
                        f"响应体超过 {MAX_DECODED_BYTES} 字节上限，拒绝该响应"
                    )
                chunks.append(chunk)
            compressed = b"".join(chunks)
            # 服务端可能用多条重复的 Content-Encoding 头（而非逗号合并），
            # .get() 只拿第一条会漏解一层；get_all 合并后交给 _decompress 逐层解。
            ce_headers = resp.headers.get_all("Content-Encoding") or []
            raw = _decompress(compressed, ",".join(ce_headers) if ce_headers else None)
            return raw, resp.headers.get_content_charset()

    try:
        raw, charset = _fetch(_SSL_CONTEXT)
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        # 部分政府/新闻站点使用不规范证书链。默认严格失败，只有显式设置
        # NEWS_INSECURE_SSL=1 才降级为不校验重试；两条路径都必须留下日志，
        # 不允许再出现“静默缴械”。
        if not _insecure_ssl_allowed():
            LOG.error(
                "SSL 证书校验失败，按默认严格策略中止：%s（%s）。"
                "确认该站点可信后可设置 %s=1 允许降级。",
                url,
                exc.reason,
                INSECURE_SSL_ENV,
            )
            raise
        LOG.warning(
            "SSL 证书校验失败，已按 %s=1 降级为不校验重试（存在中间人风险）：%s（%s）",
            INSECURE_SSL_ENV,
            url,
            exc.reason,
        )
        raw, charset = _fetch(_unverified_ssl_context())

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


def extract_from_html(
    html: str,
    url: str,
    extractor: GeneralNewsExtractor,
    *,
    allow_browser: bool = True,
    url_validator: Callable[[str], None] | None = None,
    already_rendered: bool = False,
    title_xpath: str = "",
) -> dict | None:
    """Extract one article already-fetched HTML (shared by CLI and crawler).

    Handles video-page adaptation, GNE extraction, optional browser render
    fallback and image normalization, so both entry points keep identical
    semantics instead of maintaining two diverged copies.
    """
    if is_video_page(url, html):
        video = extract_video_meta(html, url)
        if video is not None:
            LOG.info("识别为视频页，使用 meta 适配抽取。")
            return video

    try:
        result = extractor.extract(html, title_xpath=title_xpath)
    except NoContentException:
        result = None

    if result is None or content_needs_render(result.get("content")):
        if already_rendered:
            # HTML 已经过 Chromium 渲染，再启动一次只会重复等一轮；仍为空
            # 就按失败返回，正文过短则沿用下面的最短长度兜底策略。
            if result is None:
                LOG.info("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
                return None
            if content_needs_render(result.get("content")):
                if not content_is_too_short(result.get("content")):
                    LOG.info("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
                    return None
                LOG.warning("渲染回退后正文仍不足 %d 字，仍按原文返回。", MIN_CONTENT_LENGTH)
        elif not allow_browser:
            LOG.info("直接抓取未提取到正文，且浏览器回退已禁用。")
            return None
        else:
            LOG.info("直接抓取未提取到正文，正在用本机 Chrome 无头模式渲染...")
            rendered = fetch_html_with_browser(url, url_validator=url_validator)
            if rendered is None:
                LOG.info("无法提取正文。可能是列表页，或不是新闻文章页。")
                return None
            html = rendered
            try:
                result = extractor.extract(html, title_xpath=title_xpath)
            except NoContentException:
                result = None
            if result is None:
                LOG.info("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
                return None
            if content_needs_render(result.get("content")):
                if not content_is_too_short(result.get("content")):
                    LOG.info("浏览器渲染后仍无法提取正文。可能是列表页，或不是新闻文章页。")
                    return None
                LOG.warning("渲染回退后正文仍不足 %d 字，仍按原文返回。", MIN_CONTENT_LENGTH)
    # 抽取出的图片可能是 //cdn.example.com/x.jpg 这类协议相对地址，
    # 按文章 URL 补全，避免下游拿到无法直接使用的地址。
    result["images"] = normalize_images(result.get("images"), url)
    return result


def extract_article(
    url: str,
    extractor: GeneralNewsExtractor,
    *,
    allow_browser: bool = True,
    url_validator: Callable[[str], None] | None = None,
    title_xpath: str = "",
) -> dict | None:
    """抓取单个 URL 并抽取正文；必要时回退浏览器，失败或正文为空时返回 None。"""
    LOG.info("正在抓取: %s", url)
    already_rendered = False
    try:
        html = fetch_html(url, url_validator=url_validator)
    except urllib.error.HTTPError as exc:
        if exc.code not in BROWSER_FALLBACK_HTTP_CODES:
            LOG.warning("抓取失败（HTTP %s）。", exc.code)
            return None
        if not allow_browser:
            LOG.info("直接抓取被拒绝(HTTP %s)，且浏览器回退已禁用。", exc.code)
            return None
        LOG.info("直接抓取被拒绝(HTTP %s)，正在用本机 Chrome 无头模式渲染...", exc.code)
        rendered = fetch_html_with_browser(url, url_validator=url_validator)
        if rendered is None:
            LOG.warning("浏览器回退失败。")
            return None
        html = rendered
        already_rendered = True
    except (
        urllib.error.URLError,
        ValueError,
        TimeoutError,
        ConnectionError,
        http.client.HTTPException,
    ) as exc:
        LOG.warning("抓取失败（网络错误或网址无效）：%s", exc)
        return None

    return extract_from_html(
        html,
        url,
        extractor,
        allow_browser=allow_browser,
        url_validator=url_validator,
        already_rendered=already_rendered,
        title_xpath=title_xpath,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("请输入新闻文章网址: ").strip()

    if not url:
        print("未输入网址。")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        result = extract_article(url, GeneralNewsExtractor())
    finally:
        # 单 URL 命令行入口用完即退出，但显式回收更安全（被当作库调用时同理）。
        close_thread_browser()
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

