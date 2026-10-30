"""Offline smoke test used by deploy scripts.

Checks that the project can be imported, dates parse correctly, GNE can
extract from a bundled sample page, and SQLite can be initialized.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10+ is required.")

    import playwright  # noqa: F401
    from gne import GeneralNewsExtractor
    from news_crawler import init_db, load_config, parse_datetime
    import url_extract  # noqa: F401

    root = Path(__file__).resolve().parent

    date_cases = {
        "2026-09-04 10:00:00": "2026-09-04",
        "2026年09月04日 18:44:09": "2026-09-04",
        "04 2026.09": "2026-09-04",
    }
    for text, expected in date_cases.items():
        parsed = parse_datetime(text)
        if parsed is None or parsed.strftime("%Y-%m-%d") != expected:
            raise AssertionError(f"date parse failed: {text!r} -> {parsed}")

    # 视频页适配最小回归用例：防误判 / 标题截断 / 日期优先级
    ordinary_url = "https://example.com/news/2026/10/30/abc.html?related=/video/1"
    ordinary_html = (
        "<html><head><title>普通文章</title></head><body><article>"
        '<video><source type="video/mp4" src="a.mp4"></video><p>正文</p>'
        "</article></body></html>"
    )
    if url_extract.is_video_page(ordinary_url, ordinary_html):
        raise AssertionError("embedded video ordinary article misclassified as video page")

    video_html = (
        '<html><head><meta property="og:type" content="video.other">'
        '<meta property="og:title" content="COVID-19疫苗获批-中新网视频">'
        '<meta name="date" content="2026-10-30">'
        '<script type="application/ld+json">'
        '{"@type":"VideoObject","datePublished":"2020-01-05"}</script>'
        "</head><body></body></html>"
    )
    video_url = "https://example.com/shipin/2026/10-30/news1.shtml"
    if not url_extract.is_video_page(video_url, video_html):
        raise AssertionError("video page not detected")
    video_meta = url_extract.extract_video_meta(video_html, video_url)
    if video_meta is None or video_meta["title"] != "COVID-19疫苗获批":
        raise AssertionError(f"video title truncated: {video_meta}")
    if video_meta["publish_time"] != "2020-01-05":
        raise AssertionError(f"video publish priority wrong: {video_meta['publish_time']}")

    sample_html = (root / "tests" / "163" / "1.html").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    result = GeneralNewsExtractor().extract(sample_html)
    if not result.get("title") or not result.get("content"):
        raise AssertionError("GNE failed to extract sample page")

    config = load_config(str(root / "sources.json"))
    if not isinstance(config.get("sources"), list):
        raise AssertionError("sources.json is not a valid config template")

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(str(Path(tmp) / "smoke.db"))
        try:
            conn.execute("SELECT 1 FROM articles LIMIT 1")
        finally:
            conn.close()

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
