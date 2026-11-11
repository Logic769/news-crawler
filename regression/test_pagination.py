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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
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
            side_effect=lambda url, render, url_validator=None: pages[url],
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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(
                source, known_urls=known
            )

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "known_pages")
        self.assertEqual(len(calls), 2)

    def test_known_pages_stop_is_disabled_by_default(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            last_url_xpath="//a[@class='last']",
            start=2,
            max_pages=10,
            stop_when_older_than_cutoff=False,
        )
        first = _page(("旧一", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a class="last" href="index_3.html">尾页</a>',
        )
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": _page(
                ("旧二", "2026-01-02", "a2.html")
            ),
            "https://example.com/list/index_3.html": _page(
                ("旧三", "2026-01-03", "a3.html")
            ),
        }
        known = {
            "https://example.com/a1.html",
            "https://example.com/a2.html",
            "https://example.com/a3.html",
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(
                source, known_urls=known
            )

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(len(calls), 3)

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
            side_effect=lambda url, render, url_validator=None: pages[url],
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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
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
            side_effect=lambda url, render, url_validator=None: pages[url],
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
            side_effect=lambda url, render, url_validator=None: pages[url],
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
        ), mock.patch.object(
            news_crawler, "fetch_html_with_browser", return_value=None
        ) as browser:
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stop_reason"], "empty")
        self.assertEqual(browser.call_count, 3)

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
            side_effect=lambda url, render, url_validator=None: pages[url],
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

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
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
            side_effect=lambda url, render, url_validator=None: pages[url],
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
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["stop_reason"], "done")

    def test_last_url_xpath_uses_largest_candidate_page(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            last_url_xpath="//a[@class='last']",
            start=2,
            max_pages=10,
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a class="last" href="index_3.html">尾页</a>'
            '<a class="last" href="index_7.html">末页</a>'
            '<a href="index_9.html">普通链接</a>',
        )
        pages = {"https://example.com/list/": first}
        for page in range(2, 8):
            pages[f"https://example.com/list/index_{page}.html"] = _page(
                (f"第{page}页", f"2026-01-{page:02d}", f"a{page}.html")
            )

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 7)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_7.html",
        )

    def test_last_url_xpath_numeric_data_page_counts_as_page_number(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            last_url_xpath="//a[@class='last']",
            start=2,
            max_pages=13,
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a class="last" data-page="12">尾页</a>'
            '<a class="last" href="index_4.html">末页</a>',
        )
        pages = {"https://example.com/list/": first}
        for page in range(2, 13):
            pages[f"https://example.com/list/index_{page}.html"] = _page(
                (f"第{page}页", f"2026-01-{page:02d}", f"a{page}.html")
            )

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 12)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_12.html",
        )

    def test_scalar_last_url_xpath_does_not_crash(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            last_url_xpath="count(//a[@class='last'])",
            start=2,
            max_pages=2,
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a class="last" href="index_2.html">尾页</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "done")

    def test_scalar_next_url_xpath_does_not_crash(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="number(//a/@href)",
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a href="index_2.html">下一页</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "no_next")

    def test_template_max_pages_counts_list_url_as_first_page(self) -> None:
        source = _source(
            mode="template",
            url_template="https://example.com/list/index_{page}.html",
            start=5,
            max_pages=3,
            stop_when_older_than_cutoff=False,
            stop_after_known_pages=0,
        )
        pages = {
            "https://example.com/list/": _page(("第一页", "2026-01-01", "a1.html")),
            "https://example.com/list/index_5.html": _page(
                ("第五页", "2026-01-05", "a5.html")
            ),
            "https://example.com/list/index_6.html": _page(
                ("第六页", "2026-01-06", "a6.html")
            ),
        }
        calls: list[str] = []

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
            calls.append(url)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(
            calls,
            [
                "https://example.com/list/",
                "https://example.com/list/index_5.html",
                "https://example.com/list/index_6.html",
            ],
        )

    def test_next_xpath_hash_href_falls_back_to_onclick(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="//a[@id='more']",
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a id="more" href="#" '
            'onclick="window.location.href=\'index_2.html\'">更多</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_2.html",
        )

    def test_generic_next_anchor_hash_href_falls_back_to_onclick(self) -> None:
        source = _source(mode="next", stop_after_known_pages=0)
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a href="#" onclick="window.location.href=\'index_2.html\'">'
            "下一页</a>",
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_2.html",
        )

    def test_next_xpath_attribute_onclick_is_resolved(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="//a[@id='more']/@onclick",
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a id="more" onclick="window.location.href=\'index_2.html\'">'
            "更多</a>",
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_2.html",
        )

    def test_next_xpath_attribute_javascript_prefix_is_resolved(self) -> None:
        source = _source(
            mode="next",
            next_url_xpath="//a[@id='more']/@onclick",
            stop_after_known_pages=0,
        )
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a id="more" onclick="JavaScript:location=\'index_3.html\'">'
            "更多</a>",
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_3.html": second,
        }

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_3.html",
        )

    def test_rendered_page_is_used_for_next_link(self) -> None:
        source = _source(mode="next", stop_after_known_pages=0)
        raw = "<html><body></body></html>"
        rendered = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a href="index_2.html">下一页</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
            if url == "https://example.com/list/":
                return raw
            return second

        with mock.patch.object(
            news_crawler, "fetch_list_html", side_effect=fake_fetch
        ), mock.patch.object(
            news_crawler, "fetch_html_with_browser", return_value=rendered
        ) as browser:
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            result["page_urls"][-1],
            "https://example.com/list/index_2.html",
        )
        browser.assert_called_once_with(
            "https://example.com/list/", url_validator=mock.ANY
        )

    def test_page_with_unknown_date_does_not_trigger_cutoff(self) -> None:
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
            ("旧闻", "2023-01-01", "a1.html"),
            ("日期缺失", "", "a2.html"),
        )
        second = _page(("新文章", "2026-01-02", "a3.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }
        cutoff = datetime(2023, 11, 11, tzinfo=ZoneInfo("Asia/Shanghai"))

        with mock.patch.object(
            news_crawler,
            "fetch_list_html",
            side_effect=lambda url, render, url_validator=None: pages[url],
        ):
            result = news_crawler.collect_list_entries_detailed(source, cutoff=cutoff)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["stop_reason"], "done")

    def test_pagination_validator_allows_configured_private_host(self) -> None:
        source = _source()
        source["list_url"] = "http://127.0.0.1/list/"
        validator = news_crawler._pagination_url_validator(source)
        validator("http://127.0.0.1/list/index_2.html")

    def test_pagination_validator_rejects_untrusted_private_ip(self) -> None:
        source = _source()
        validator = news_crawler._pagination_url_validator(source)
        with mock.patch.object(
            news_crawler.socket,
            "getaddrinfo",
            side_effect=OSError("dns blocked in test"),
        ):
            with self.assertRaises(ValueError):
                validator("http://127.0.0.1/admin")

    def test_pagination_validator_allows_public_ip_literal(self) -> None:
        source = _source()
        validator = news_crawler._pagination_url_validator(source)
        validator("https://8.8.8.8/list/index_2.html")

    def test_pagination_validator_allows_ip_resolved_from_trusted_host(self) -> None:
        # 可信主机名被 DNS 解析到内网 IP 时，直接写该 IP 的下一页 URL 也应放行。
        source = _source()
        source["list_url"] = "http://intranet.example/list/"
        validator = news_crawler._pagination_url_validator(source)
        fake_infos = [(2, 1, 6, "", ("10.1.2.3", 0))]
        with mock.patch.object(
            news_crawler.socket, "getaddrinfo", return_value=fake_infos
        ) as resolver:
            validator("http://10.1.2.3/list/index_2.html")
        resolver.assert_called()

    def test_collect_passes_validator_to_list_fetch(self) -> None:
        source = _source(mode="next", stop_after_known_pages=0)
        first = _page(("第一页", "2026-01-01", "a1.html")).replace(
            "</ul>",
            '</ul><a href="index_2.html">下一页</a>',
        )
        second = _page(("第二页", "2026-01-02", "a2.html"))
        pages = {
            "https://example.com/list/": first,
            "https://example.com/list/index_2.html": second,
        }
        seen: list[object] = []

        def fake_fetch(url: str, render: bool, url_validator=None) -> str:
            seen.append(url_validator)
            return pages[url]

        with mock.patch.object(news_crawler, "fetch_list_html", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(seen), 2)
        for validator in seen:
            self.assertTrue(callable(validator))

    def test_invalid_pagination_mode_falls_back_to_none(self) -> None:
        source = _source(mode="unexpected")
        html = _page(("一", "2026-01-01", "a1.html"))
        with mock.patch.object(news_crawler, "fetch_list_html", return_value=html):
            result = news_crawler.collect_list_entries_detailed(source)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(len(result["items"]), 1)


if __name__ == "__main__":
    unittest.main()
