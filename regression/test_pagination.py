"""列表分页抓取回归：模板分页、下一页、截止日期与已入库页早停。"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import news_crawler


def _page(*items: tuple[str, str, str]) -> str:
    rows = "".join(
        f'<li><a href="/{url}">{title}</a><span class="time">{date}</span></li>'
        for title, date, url in items
    )
    return f'<html><body><ul class="list">{rows}</ul></body></html>'


def _source(**pagination) -> dict:
    base = {
        "name": "demo",
        "list_url": "https://example.com/list/",
        "item_xpath": "//ul[@class='list']/li",
        "title_xpath": ".//a",
        "url_xpath": ".//a/@href",
        "date_xpath": ".//span[@class='time']",
        "date_format": "%Y-%m-%d",
        "timezone": "Asia/Shanghai",
        "delay": 0,
    }
    if pagination:
        base["pagination"] = pagination
    return base


class PaginationTest(unittest.TestCase):
    def test_template_stops_after_oldest_date_before_cutoff(self) -> None:
        html = _page(
            ("旧闻一", "2023-09-12", "a1.html"),
            ("旧闻二", "2023-10-01", "a2.html"),
        )
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            total_pages_regex=r"index_(\d+)\.html",
            start=2,
            max_pages=50,
            stop_when_older_than_cutoff=True,
            stop_after_known_pages=0,
        )
        cutoff = datetime(2023, 11, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return html

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(
                source, cutoff=cutoff
            )

        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["stop_reason"], "cutoff")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(calls, ["https://example.com/list/"])

    def test_template_follows_generated_pages(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            total_pages_regex=r"index_(\d+)\.html",
            start=2,
            max_pages=2,
            stop_when_older_than_cutoff=False,
            stop_after_known_pages=0,
        )
        pages = {
            "https://example.com/list/": _page(("第一页", "2026-01-01", "a1.html")),
            "https://example.com/list/index_2.html": _page(
                ("第二页", "2026-01-02", "a2.html")
            ),
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            calls,
            [
                "https://example.com/list/",
                "https://example.com/list/index_2.html",
            ],
        )

    def test_next_mode_respects_max_pages(self) -> None:
        source = _source(mode="next", max_pages=2, stop_after_known_pages=0)
        pages = {
            "https://example.com/list/": _page(("第一页", "2026-01-01", "a1.html")).replace(
                "</ul>",
                '</ul><a href="index_2.html">下一页</a>',
            ),
            "https://example.com/list/index_2.html": _page(
                ("第二页", "2026-01-02", "a2.html")
            ).replace("</ul>", '</ul><a href="index_3.html">下一页</a>'),
            "https://example.com/list/index_3.html": _page(
                ("第三页", "2026-01-03", "a3.html")
            ),
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "max_pages")
        self.assertEqual(len(calls), 2)

    def test_next_mode_follows_onclick_next_link(self) -> None:
        source = _source(mode="next", stop_after_known_pages=0)
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a onclick="window.location.href=encodeURI(\'index_2.html\')">下一页</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {"https://example.com/list/": first, "https://example.com/list/index_2.html": second}

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "no_next")
        self.assertEqual([item["url"] for item in result["items"]], [
            "https://example.com/a1.html",
            "https://example.com/a2.html",
        ])

    def test_known_pages_stop_after_configured_streak(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            total_pages_regex=r"index_(\d+)\.html",
            start=2,
            max_pages=10,
            stop_when_older_than_cutoff=False,
            stop_after_known_pages=2,
        )
        pages = {
            "https://example.com/list/": _page(("旧一", "2026-01-01", "a1.html")),
            "https://example.com/list/index_2.html": _page(
                ("旧二", "2026-01-02", "a2.html")
            ),
        }
        known = {
            "https://example.com/a1.html",
            "https://example.com/a2.html",
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(
                source, known_urls=known
            )

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "known_pages")
        self.assertEqual(len(calls), 2)

    def test_list_urls_keeps_legacy_fixed_url_behavior(self) -> None:
        source = _source()
        source.pop("list_url")
        source["list_urls"] = [
            "https://example.com/list/1.html",
            "https://example.com/list/2.html",
        ]
        pages = {
            "https://example.com/list/1.html": _page(("一", "2026-01-01", "a1.html")),
            "https://example.com/list/2.html": _page(("二", "2026-01-02", "a2.html")),
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 2)

    def test_list_urls_ignores_pagination_config(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            start=2,
            max_pages=2,
        )
        source.pop("list_url")
        source["list_urls"] = [
            f"https://example.com/list/{index}.html" for index in range(1, 13)
        ]
        pages = {
            url: _page((f"第{index}页", "2026-01-01", f"a{index}.html"))
            for index, url in enumerate(source["list_urls"], start=1)
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 12)
        self.assertEqual(len(result["items"]), 12)
        self.assertEqual(calls, list(pages))

    def test_list_urls_ignores_cutoff_stop(self) -> None:
        source = _source()
        source.pop("list_url")
        source["list_urls"] = [
            "https://example.com/list/1.html",
            "https://example.com/list/2.html",
            "https://example.com/list/3.html",
        ]
        pages = {
            "https://example.com/list/1.html": _page(("旧", "2020-01-01", "a1.html")),
            "https://example.com/list/2.html": _page(("新", "2026-11-10", "a2.html")),
            "https://example.com/list/3.html": _page(("新二", "2026-11-09", "a3.html")),
        }
        cutoff = datetime(2026, 11, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source, cutoff=cutoff)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["stop_reason"], "done")

    def test_fixed_list_fetch_failure_does_not_stick(self) -> None:
        source = _source()
        source.pop("list_url")
        source["list_urls"] = [
            "https://example.com/list/1.html",
            "https://example.com/list/2.html",
        ]
        pages = {
            "https://example.com/list/1.html": "",
            "https://example.com/list/2.html": _page(("二", "2026-01-02", "a2.html")),
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["stop_reason"], "done")

    def test_fixed_list_all_empty_pages_reports_empty(self) -> None:
        source = _source()
        source.pop("list_url")
        source["list_urls"] = [
            "https://example.com/list/1.html",
            "https://example.com/list/2.html",
            "https://example.com/list/3.html",
        ]
        empty = _page()

        with mock.patch.object(
            news_crawler, "fetch_list_html", return_value=empty
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stop_reason"], "empty")

    def test_pinned_old_item_does_not_stop_next_page_before_cutoff(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            total_pages_regex=r"index_(\d+)\.html",
            start=2,
            max_pages=2,
            stop_when_older_than_cutoff=True,
            stop_after_known_pages=0,
        )
        first = _page(
            ("置顶旧闻", "2020-01-01", "pinned.html"),
            ("新文章", "2026-11-11", "a1.html"),
        )
        second = _page(("次新文章", "2026-11-10", "a2.html"))
        pages = {"https://example.com/list/": first, "https://example.com/list/index_2.html": second}
        cutoff = datetime(2026, 11, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source, cutoff=cutoff)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["stop_reason"], "done")

    def test_next_xpath_text_without_href_is_ignored(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="//span[@id='nx']",
            stop_after_known_pages=0,
        )
        html = _page(("一", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><span id="nx">下一页</span>',
        )

        with mock.patch.object(news_crawler, "fetch_list_html", return_value=html):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["stop_reason"], "no_next")
        self.assertEqual(result["page_urls"], ["https://example.com/list/"])

    def test_template_without_page_placeholder_falls_back_to_none(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/fixed.html",
            start=2,
            max_pages=10,
        )
        calls: list[str] = []

        def fake_fetch(url: str, render: bool) -> str:
            calls.append(url)
            return _page(("一", "2026-01-01", "a1.html"))

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(calls, ["https://example.com/list/"])

    def test_collect_list_entries_wrapper_returns_list(self) -> None:
        source = _source()
        html = _page(("一", "2026-01-01", "a1.html"))
        with mock.patch.object(news_crawler, "fetch_list_html", return_value=html):
            result = news_crawler.collect_list_entries(source)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)

    def test_next_xpath_element_href_is_used(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="//a[@id='more']",
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a id="more" href="index_2.html">更多</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {"https://example.com/list/": first, "https://example.com/list/index_2.html": second}

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "no_next")

    def test_last_url_xpath_element_href_is_used(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            last_url_xpath="//a[@id='last']",
            start=2,
            max_pages=10,
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a id="last" href="index_3.html">尾页</a>',
        )
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": _page(
                ("第二页", "2026-01-02", "a2.html")
            ),
            "https://example.com/list/index_3.html": _page(
                ("第三页", "2026-01-03", "a3.html")
            ),
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["stop_reason"], "done")

    def test_invalid_pagination_mode_falls_back_to_none(self) -> None:
        source = _source(mode="unexpected")
        html = _page(("一", "2026-01-01", "a1.html"))
        with mock.patch.object(news_crawler, "fetch_list_html", return_value=html):
            result = news_crawler.collect_list_entries_detailed(source)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(len(result["items"]), 1)


if __name__ == "__main__":
    unittest.main()
