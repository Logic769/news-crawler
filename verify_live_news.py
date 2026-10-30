"""Live news extraction verification used by CI.

The check deliberately hits public news pages (not local fixtures):
- 5 static text articles: direct HTTP fetch + GNE extraction
- 3 JavaScript-rendered articles: direct HTML is insufficient; headless
  Chromium rendering + GNE extraction must succeed
- 2 video pages: the url_extract video branch must identify the page and
  return title/publish time

Pools are curated from stable news sites. Each run randomly selects the
requested count from each pool; the chosen seed is printed so a failing run
can be reproduced with --seed or LIVE_NEWS_SEED.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

from gne import GeneralNewsExtractor, NoContentException

import url_extract


MIN_STATIC_CONTENT = 100
MIN_RENDERED_CONTENT = 200

# 5 个静态文本页从以下站点中随机抽取（均为直接 HTTP 即可抽取正文的页面）
STATIC_POOL = (
    "https://www.chinanews.com.cn/gn/2026/10-30/10723204.shtml",
    "https://www.chinanews.com.cn/ty/2026/10-30/10723200.shtml",
    "https://news.sina.com.cn/c/2026-10-30/doc-iniwxzqi4867819.shtml",
    "https://news.sina.com.cn/gov/xlxw/2026-10-30/doc-iniwyfwc8363041.shtml",
    "https://sports.sina.com.cn/l/2026-10-30/doc-iniwyncy2327952.shtml",
    "https://www.chinanews.com.cn/cj/2026/10-30/10723232.shtml",
    "https://www.chinanews.com.cn/sh/2026/10-30/10723221.shtml",
    "https://www.chinanews.com.cn/gn/2026/10-30/10723215.shtml",
)

# 3 个动态 JS 渲染页：直接抓取正文不足，必须由 Chromium 渲染后才能抽取
DYNAMIC_POOL = (
    "https://news.cctv.com/2026/10/30/ARTIBWOGaadLbdChpDPhblyt261030.shtml",
    "https://news.cctv.com/2026/10/30/ARTIe0N25T5q8Q8jogffSnOc261029.shtml",
    "https://news.cctv.com/2026/10/30/ARTIZpZEh3a2ocOsZYX3GKsb261029.shtml",
)

# 2 个视频页：命中 /shipin/ 路径，走 url_extract.extract_article 的视频分支
VIDEO_POOL = (
    "https://www.chinanews.com.cn/sh/shipin/2026/10-30/news1073348.shtml",
    "https://www.chinanews.com.cn/sh/shipin/2026/10-30/news1073347.shtml",
    "https://www.chinanews.com.cn/sh/shipin/cns/2026/10-30/news1073350.shtml",
    "https://www.chinanews.com.cn/cj/shipin/2026/10-30/news1073346.shtml",
)


def _content(result: dict | None) -> str:
    if not result:
        return ""
    return (result.get("content") or "").strip()


def _extract(html: str, extractor: GeneralNewsExtractor) -> dict | None:
    try:
        return extractor.extract(html)
    except NoContentException:
        return None


def _require_article(
    result: dict | None,
    url: str,
    min_length: int,
    label: str,
) -> int:
    if result is None:
        raise AssertionError(f"{url}: {label} extraction returned no result")
    title = (result.get("title") or "").strip()
    content_len = len(_content(result))
    if not title:
        raise AssertionError(f"{url}: {label} extraction returned empty title")
    if content_len < min_length:
        raise AssertionError(
            f"{url}: {label} content too short: {content_len} < {min_length}"
        )
    return content_len


def _with_retry(label: str, fn, attempts: int):
    last = None
    for index in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - report live-network failures clearly
            last = exc
            print(f"[WARN] {label} attempt {index + 1}/{attempts} failed: {exc}")
            if index + 1 < attempts:
                time.sleep(3)
    raise last  # type: ignore[misc]


def check_static(url: str, extractor: GeneralNewsExtractor) -> dict:
    html = url_extract.fetch_html(url)
    result = _extract(html, extractor)
    content_len = _require_article(result, url, MIN_STATIC_CONTENT, "static")
    return {
        "url": url,
        "title": result.get("title", "").strip(),
        "content_len": content_len,
    }


def check_dynamic(url: str, extractor: GeneralNewsExtractor) -> dict:
    direct_len = 0
    try:
        direct_html = url_extract.fetch_html(url)
        direct_result = _extract(direct_html, extractor)
        direct_len = len(_content(direct_result))
    except Exception as exc:  # noqa: BLE001
        print(f"[INFO] {url}: direct fetch failed before render: {exc}")

    if direct_len >= MIN_RENDERED_CONTENT:
        raise AssertionError(
            f"{url}: direct extraction already returned {direct_len} chars; "
            "this URL is not a JavaScript-rendered page"
        )

    rendered_html = url_extract.fetch_html_with_browser(url)
    if not rendered_html:
        raise AssertionError(f"{url}: browser rendering returned no HTML")
    result = _extract(rendered_html, extractor)
    content_len = _require_article(
        result, url, MIN_RENDERED_CONTENT, "browser-rendered"
    )
    return {
        "url": url,
        "title": result.get("title", "").strip(),
        "direct_content_len": direct_len,
        "rendered_content_len": content_len,
    }


def check_video(url: str, extractor: GeneralNewsExtractor) -> dict:
    result = url_extract.extract_article(url, extractor)
    if not result:
        raise AssertionError(f"{url}: video extraction returned no result")
    if not result.get("is_video"):
        raise AssertionError(f"{url}: not identified as a video page")
    title = (result.get("title") or "").strip()
    publish_time = (result.get("publish_time") or "").strip()
    if not title:
        raise AssertionError(f"{url}: video title is empty")
    if not publish_time:
        raise AssertionError(f"{url}: video publish_time is empty")
    return {
        "url": url,
        "title": title,
        "publish_time": publish_time,
    }


def _sample(pool: tuple[str, ...], count: int, rng: random.Random, label: str) -> list[str]:
    if count < 0:
        raise SystemExit(f"{label} count must be non-negative")
    if count > len(pool):
        raise SystemExit(
            f"{label} count {count} exceeds pool size {len(pool)}; "
            "add more URLs to the pool first"
        )
    return rng.sample(list(pool), count)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify live news extraction: static, JS-rendered and video pages."
    )
    parser.add_argument("--static", type=int, default=5, help="静态文本页数量（默认 5）")
    parser.add_argument("--dynamic", type=int, default=3, help="动态 JS 渲染页数量（默认 3）")
    parser.add_argument("--video", type=int, default=2, help="视频页数量（默认 2）")
    parser.add_argument("--seed", default=None, help="随机种子；也可用环境变量 LIVE_NEWS_SEED")
    parser.add_argument("--attempts", type=int, default=2, help="每个页面的尝试次数（默认 2）")
    args = parser.parse_args()

    seed = args.seed or os.environ.get("LIVE_NEWS_SEED")
    if not seed:
        seed = str(random.SystemRandom().randint(1, 1_000_000))
    rng = random.Random(seed)
    print(f"LIVE NEWS SEED: {seed}")

    extractor = GeneralNewsExtractor()
    selected = {
        "static": _sample(STATIC_POOL, args.static, rng, "static"),
        "dynamic": _sample(DYNAMIC_POOL, args.dynamic, rng, "dynamic"),
        "video": _sample(VIDEO_POOL, args.video, rng, "video"),
    }

    summary: dict[str, list[dict]] = {"static": [], "dynamic": [], "video": []}
    checks = (
        ("static", check_static),
        ("dynamic", check_dynamic),
        ("video", check_video),
    )

    try:
        for category, check in checks:
            urls = selected[category]
            print(f"\n== {category}: {len(urls)} page(s) ==")
            for index, url in enumerate(urls, 1):
                print(f"[{category} {index}/{len(urls)}] {url}")
                result = _with_retry(
                    f"{category} {url}",
                    lambda url=url, check=check: check(url, extractor),
                    args.attempts,
                )
                summary[category].append(result)
                if category == "video":
                    detail = f"发布时间 {result['publish_time']}"
                else:
                    detail = f"{result.get('content_len', result.get('rendered_content_len', 0))} 字"
                print(f"  OK: {result['title']}（{detail}）")
    except Exception as exc:  # noqa: BLE001
        print(f"\nLIVE NEWS VERIFICATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("\nLIVE NEWS VERIFICATION PASSED")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
