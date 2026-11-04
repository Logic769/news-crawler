"""Live news extraction verification used by CI.

The check deliberately hits public news pages (not local fixtures):
- static text articles: direct HTTP fetch + GNE extraction
- JavaScript-rendered articles: direct HTML is insufficient; headless
  Chromium rendering + GNE extraction must succeed
- video pages: the url_extract video branch must identify the page and
  return title/publish time

Pools live in live_news_pool.json next to this file (static / dynamic / video
arrays). The built-in pools below are only a fallback used when that file is
missing or a category is empty. Each run shuffles the pool and checks pages
until the requested count passes; a failed page is replaced from the rest of
the pool (deterministic content assertions are never retried on the same URL).
The chosen seed is printed so a failing run can be reproduced with --seed or
LIVE_NEWS_SEED.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import url_extract
from gne import GeneralNewsExtractor, NoContentException

MIN_STATIC_CONTENT = 100
MIN_RENDERED_CONTENT = 200
POOL_FILE = Path(__file__).with_name("live_news_pool.json")


class ContentAssertion(AssertionError):
    """确定性的内容判定失败；不重试，避免浪费时间。"""


# 内置兜底池：仅在 live_news_pool.json 缺失或某类为空时使用。
# 每类至少 10 条、尽量覆盖不同来源站点，保证默认目标（10/10/10）在池文件
# 缺失时仍可运行；否则 run_category 会因 count>len(pool) 直接退出。
STATIC_POOL = (
    "https://theory.southcn.com/node_93e45a46ed/c22d7980cd.shtml",
    "https://news.cri.cn/20261029/b4e3b263-f485-4a6e-903d-8e07498ea4ae.html",
    "https://news.dahe.cn/2026/10-30/2146949.html",
    "https://news.youth.cn/gn/202610/t20261030_16940410.htm",
    "https://www.163.com/data/article/LAGN922R000181IU.html",
    "https://www.sohu.com/a/1094741833_121333745",
    "https://news.ifeng.com/c/8xeUGvcPuay",
    "https://news.southcn.com/node_47a8059d6c/a7e1c0a6d8.shtml",
    "https://www.thepaper.cn/newsDetail_forward_34370855",
    "https://news.iqilu.com/china/gedi/2026/1030/5957425.shtml",
)

DYNAMIC_POOL = (
    "https://news.dahe.cn/2026/10-30/2146949.html",
    "https://politics.gmw.cn/2026-10/30/content_39076960.htm",
    "http://v.china.com.cn/2026-10/16/content_118737632.html",
    "https://news.jschina.com.cn/yw/202610/t20261030_s6ae4996de4b08f4f4fbdd096.shtml",
    "https://news.cctv.com/2026/10/30/ARTIqDabPYgGEgNv1RkGFGzy261029.shtml",
    "https://www.sz.gov.cn/cn/xxgk/zfxxgj/zwdt/content/post_13046708.html",
    "https://xapp.southcn.com/app/activity.html?id=44150",
    "https://nw.eastday.com/self/yw/20261030/bbf4e3e5c52d4bbead83903015d09fb5.html",
    "http://www.people.com.cn/n/2013/1227/c373005-23961344.html",
    "https://zjnews.zjol.com.cn/yc/qmt/202610/t20261030_31999544.shtml",
)

VIDEO_POOL = (
    "http://www.news.cn/video/20261022/3445a3e5c0e64a3e829899c9d72d4867/c.html",
    "https://www.kankanews.com/detail/M8Q807kp8QL",
    "https://k.sina.com.cn/article_1887790981_m7085678503301e71w.html?from=news",
    "https://www.yntv.cn/video/20261030/1793369472827831.html",
    "https://www.thepaper.cn/newsDetail_forward_34369814",
    "https://www.163.com/v/video/VG9UI7R4F.html",
    "https://www.northnews.cn/video/2026/1103/2531840.html",
    "http://www.ahwang.cn/video/2026/1103/3072063.html",
    "https://www.chinanews.com.cn/sh/shipin/cns/2026/11-03/news1073659.shtml",
    "http://www.cnwest.com/shipin/a/2025/12/09/23253101.html",
)


def load_pool() -> dict[str, tuple[str, ...]]:
    """优先读取 live_news_pool.json，缺失时回退内置池。"""
    pools: dict[str, tuple[str, ...]] = {
        "static": STATIC_POOL,
        "dynamic": DYNAMIC_POOL,
        "video": VIDEO_POOL,
    }
    if not POOL_FILE.is_file():
        print(f"[INFO] 未找到 {POOL_FILE.name}，使用内置池")
        return pools
    try:
        raw = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[WARN] 读取 {POOL_FILE.name} 失败（{exc}），使用内置池")
        return pools
    for key in pools:
        urls = [u.strip() for u in (raw.get(key) or []) if isinstance(u, str) and u.strip()]
        if urls:
            pools[key] = tuple(dict.fromkeys(urls))
        else:
            print(f"[WARN] {POOL_FILE.name} 缺少 {key} 池，使用内置池")
    print(
        "POOL SIZES: "
        + ", ".join(f"{k}={len(v)}" for k, v in pools.items())
    )
    return pools


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
        raise ContentAssertion(f"{url}: {label} extraction returned no result")
    title = (result.get("title") or "").strip()
    content_len = len(_content(result))
    if not title:
        raise ContentAssertion(f"{url}: {label} extraction returned empty title")
    if content_len < min_length:
        raise ContentAssertion(
            f"{url}: {label} content too short: {content_len} < {min_length}"
        )
    return content_len


def _with_retry(label: str, fn, attempts: int):
    if attempts < 1:
        raise SystemExit("--attempts must be at least 1")
    last = None
    for index in range(attempts):
        try:
            return fn()
        except ContentAssertion:
            # 内容判定失败是确定性的，重试只会浪费时间；网络/浏览器失败仍会重试
            raise
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
    assert result is not None  # _require_article 已保证
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
        # 页面改版为服务端渲染不应让 CI 失败；仍继续验证 Chromium 路径。
        print(
            f"[WARN] {url}: direct extraction already returned {direct_len} chars; "
            "continuing to verify browser rendering anyway"
        )

    rendered_html = url_extract.fetch_html_with_browser(url)
    if not rendered_html:
        raise AssertionError(f"{url}: browser rendering returned no HTML")
    result = _extract(rendered_html, extractor)
    content_len = _require_article(
        result, url, MIN_RENDERED_CONTENT, "browser-rendered"
    )
    assert result is not None  # _require_article 已保证
    return {
        "url": url,
        "title": result.get("title", "").strip(),
        "direct_content_len": direct_len,
        "rendered_content_len": content_len,
    }


def check_video(url: str, extractor: GeneralNewsExtractor) -> dict:
    result = url_extract.extract_article(url, extractor)
    if not result:
        # 可能是网络/浏览器瞬时失败，交给 _with_retry 重试
        raise AssertionError(f"{url}: video extraction returned no result")
    if not result.get("is_video"):
        raise ContentAssertion(f"{url}: not identified as a video page")
    title = (result.get("title") or "").strip()
    publish_time = (result.get("publish_time") or "").strip()
    if not title:
        raise ContentAssertion(f"{url}: video title is empty")
    if not publish_time:
        raise ContentAssertion(f"{url}: video publish_time is empty")
    return {
        "url": url,
        "title": title,
        "publish_time": publish_time,
    }


def run_category(
    category: str,
    check,
    pool: tuple[str, ...],
    count: int,
    rng: random.Random,
    attempts: int,
    extractor: GeneralNewsExtractor,
) -> tuple[list[dict], list[dict]]:
    """随机抽取 count 个页面并逐个校验；失败时从剩余池补抽。"""
    if count < 0:
        raise SystemExit(f"{category} count must be non-negative")
    if not pool:
        raise SystemExit(f"{category}: 页面池为空，无法验证；请补充 live_news_pool.json")
    if count > len(pool):
        # 池不足时必须失败：静默把 10/10/10 降到更少会让 CI 的硬性门槛
        # 被悄悄放宽。内置兜底池每类已保证 >=10 条，正常情况下不会触发。
        raise SystemExit(
            f"{category}: 目标 {count} 页超过可用池 {len(pool)}，"
            "请补充 live_news_pool.json 或降低 --static/--dynamic/--video"
        )
    queue = list(pool)
    rng.shuffle(queue)
    # 每个目标页最多额外尝试 2 个替补页面，避免单个失效 URL 拖长 CI。
    budget = min(len(queue), count * 3)
    passed: list[dict] = []
    failed: list[dict] = []
    print(f"\n== {category}: 目标 {count} 页（池 {len(pool)}，最多检查 {budget}）==")

    while len(passed) < count and queue and len(passed) + len(failed) < budget:
        url = queue.pop()
        index = len(passed) + len(failed) + 1
        print(f"[{category} {index}/{budget}] {url}")
        try:
            result = _with_retry(
                f"{category} {url}",
                lambda url=url: check(url, extractor),
                attempts,
            )
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": url, "error": str(exc)})
            print(f"  FAIL: {exc}", file=sys.stderr)
            continue
        passed.append(result)
        if category == "video":
            detail = f"发布时间 {result['publish_time']}"
        else:
            detail = f"{result.get('content_len', result.get('rendered_content_len', 0))} 字"
        print(f"  OK: {result['title']}（{detail}）")

    if len(passed) < count:
        raise SystemExit(
            f"{category}: 只有 {len(passed)}/{count} 个页面通过校验，"
            "请检查池中 URL 是否失效"
        )
    return passed, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify live news extraction: static, JS-rendered and video pages."
    )
    parser.add_argument("--static", type=int, default=10, help="静态文本页数量（默认 10）")
    parser.add_argument("--dynamic", type=int, default=10, help="动态 JS 渲染页数量（默认 10）")
    parser.add_argument("--video", type=int, default=10, help="视频页数量（默认 10）")
    parser.add_argument("--seed", default=None, help="随机种子；也可用环境变量 LIVE_NEWS_SEED")
    parser.add_argument("--attempts", type=int, default=2, help="每个页面的尝试次数（默认 2）")
    parser.add_argument(
        "--pool-file",
        default=None,
        help="池文件路径（默认使用脚本同目录的 live_news_pool.json）",
    )
    args = parser.parse_args()

    global POOL_FILE
    if args.pool_file:
        POOL_FILE = Path(args.pool_file)

    seed = args.seed or os.environ.get("LIVE_NEWS_SEED")
    if not seed:
        seed = str(random.SystemRandom().randint(1, 1_000_000))
    rng = random.Random(seed)
    print(f"LIVE NEWS SEED: {seed}")

    extractor = GeneralNewsExtractor()
    pools = load_pool()
    summary: dict[str, list[dict]] = {"static": [], "dynamic": [], "video": []}
    failures: dict[str, list[dict]] = {"static": [], "dynamic": [], "video": []}
    targets = {
        "static": args.static,
        "dynamic": args.dynamic,
        "video": args.video,
    }

    try:
        for category, check in (
            ("static", check_static),
            ("dynamic", check_dynamic),
            ("video", check_video),
        ):
            passed, failed = run_category(
                category,
                check,
                pools[category],
                targets[category],
                rng,
                args.attempts,
                extractor,
            )
            summary[category] = passed
            failures[category] = failed
    except Exception as exc:  # noqa: BLE001
        print(f"\nLIVE NEWS VERIFICATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("\nLIVE NEWS VERIFICATION PASSED")
    print(
        "PASSED COUNTS: "
        + ", ".join(f"{k}={len(v)}" for k, v in summary.items())
    )
    if any(failures.values()):
        print("REPLACED PAGES: " + json.dumps(failures, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
