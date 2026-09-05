"""Fetch the latest 5 NSFC business-information items and extract them with GNE."""

import json
import re
import ssl
import urllib.request
from urllib.parse import urljoin

import lxml.html
from gne import GeneralNewsExtractor

LIST_URL = "https://www.nsfc.gov.cn/p1/3381/2822/tzsm1.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_html(url: str) -> str:
    ctx = ssl._create_unverified_context()  # site uses a non-standard cert chain
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "ignore")


def parse_latest_entries(html: str, limit: int = 5) -> list[dict]:
    doc = lxml.html.fromstring(html)
    uls = doc.xpath('//ul[contains(@class, "wzy-ul")]')
    if not uls:
        return []
    items = uls[0].xpath('.//li[contains(@class, "wzy-item")]')
    entries = []
    for li in items:
        links = li.xpath('.//a[contains(@href, ".html")]')
        if not links:
            continue
        a = links[0]
        text = " ".join((a.text_content() or "").split())
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", li.text_content() or "")
        date = date_match.group(0) if date_match else ""
        entries.append(
            {
                "title": text,
                "url": urljoin(LIST_URL, a.get("href")),
                "date": date,
            }
        )
    entries.sort(key=lambda item: item["date"], reverse=True)
    return entries[:limit]


def main() -> None:
    list_html = fetch_html(LIST_URL)
    latest = parse_latest_entries(list_html)
    print("LATEST_COUNT:", len(latest))
    extractor = GeneralNewsExtractor()
    for entry in latest:
        print("\n" + "=" * 80)
        print("LIST_TITLE:", entry["title"])
        print("LIST_DATE:", entry["date"])
        print("URL:", entry["url"])
        try:
            article_html = fetch_html(entry["url"])
            result = extractor.extract(article_html)
            summary = {
                "title": result.get("title", ""),
                "publish_time": result.get("publish_time", ""),
                "author": result.get("author", ""),
                "content_length": len(result.get("content", "")),
                "content_preview": result.get("content", "")[:180],
                "images_count": len(result.get("images", [])),
            }
            print("EXTRACT:", json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception as exc:
            print("EXTRACT_ERROR:", type(exc).__name__, str(exc))


if __name__ == "__main__":
    main()
