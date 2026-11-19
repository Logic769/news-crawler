"""datastore 列表源与 article_body_mode=container 回归。

覆盖两类新增能力：
1. 大汉版通等 CMS 的首页 CDATA + dataproxy POST 翻页；
2. 多段正文页面按容器取全文，避免 GNE 密度算法只取一段。

重点锁住配置归一、条目/日期解析、翻页停止条件、正文合并与两条
process_entry / API extract 入口的透传。
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import news_crawler
import url_extract
from gne.exceptions import NoContentException
from news_api import tasks
from news_api.settings import Settings

CUTOFF = dt.datetime(2026, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

FIRST_HTML = (
    '<html><body><script type="text/xml"><datastore>'
    '<totalrecord>3</totalrecord><recordset>'
    '<record><![CDATA[<li><a href="/p/1.html" title="第一条">第一条</a>'
    '<span class="pull-right">2026-11-10</span></li>]]></record>'
    '<record><![CDATA[<li><a href="/p/2.html" title="第二条">第二条</a>'
    '<span class="pull-right">2026-11-11</span></li>]]></record>'
    '</recordset></datastore></script></body></html>'
)

SECOND_XML = (
    '<datastore><totalrecord>3</totalrecord><recordset>'
    '<record><![CDATA[<li><a href="/p/3.html" title="第三条">第三条</a>'
    '<span class="pull-right">2026-11-12</span></li>]]></record>'
    '</recordset></datastore>'
)

CONTAINER_HTML = """<html><body><div id="zoom">
<script>alert('bad')</script><style>.bad{display:none}</style>
<p>第一段正文</p><div>第二段 <b>加粗</b></div>
<img src="//cdn.example.com/a.jpg"></div></body></html>"""


def _datastore_source(**datastore) -> dict:
    base = {
        "url": "https://example.com/module/web/jpage/dataproxy.jsp",
        "method": "POST",
        "params": {"col": "1", "columnid": "359318"},
        "page_size": 2,
        "max_pages": 3,
        "item_xpath": "//li",
        "stop_when_older_than_cutoff": True,
    }
    base.update(datastore)
    return {
        "name": "datastore_demo",
        "list_url": "https://example.com/col/index.html",
        "item_xpath": "//li",
        "title_xpath": "./a/@title",
        "url_xpath": "./a/@href",
        "date_xpath": './span[contains(@class,"pull-right")]',
        "date_format": "%Y-%m-%d",
        "timezone": "Asia/Shanghai",
        "delay": 0,
        "datastore": base,
    }


class DatastoreSettingsTest(unittest.TestCase):
    def test_defaults_and_method_are_normalized(self) -> None:
        settings = news_crawler._datastore_settings(
            {"name": "demo", "datastore": {"url": "https://example.com/dataproxy"}}
        )
        assert settings is not None
        self.assertEqual(settings["method"], "POST")
        self.assertEqual(settings["page_size"], 25)
        self.assertEqual(settings["record_regex"], r"<record>\s*<!\[CDATA\[(.*?)\]\]>\s*</record>")
        # 幂等：第二次调用直接复用同一个归一结果，不再告警。
        self.assertIs(settings, news_crawler._datastore_settings({"datastore": settings}))

    def test_invalid_method_falls_back_to_post(self) -> None:
        with self.assertLogs("news_crawler", level="WARNING") as captured:
            settings = news_crawler._datastore_settings(
                {
                    "name": "demo",
                    "datastore": {"url": "https://example.com/dataproxy", "method": "PUT"},
                }
            )
        assert settings is not None
        self.assertEqual(settings["method"], "POST")
        self.assertTrue(any("datastore.method" in line for line in captured.output))

    def test_page_url_contains_paging_params(self) -> None:
        settings = news_crawler._datastore_settings(
            {
                "name": "demo",
                "datastore": {
                    "url": "https://example.com/dataproxy?x=1",
                    "params": {"col": "1"},
                    "page_size": 25,
                },
            }
        )
        assert settings is not None
        url = news_crawler._datastore_page_url(settings, 26, 50)
        self.assertIn("startrecord=26", url)
        self.assertIn("endrecord=50", url)
        self.assertIn("perpage=25", url)
        self.assertIn("col=1", url)


class DatastoreCollectTest(unittest.TestCase):
    def _collect(self, source: dict, first_html: str, second_xml: str, **kwargs):
        calls: list[str] = []
        self.fetch_text = mock.Mock(return_value=second_xml)

        with mock.patch.object(
            news_crawler, "fetch_list_html", return_value=first_html
        ) as fetch_list, mock.patch.object(
            url_extract, "fetch_text", self.fetch_text
        ):
            result = news_crawler.collect_list_entries_detailed(source, **kwargs)
        return result, fetch_list, calls

    def test_collects_first_page_and_dataproxy_pages(self) -> None:
        result, fetch_list, _ = self._collect(
            _datastore_source(), FIRST_HTML, SECOND_XML, cutoff=CUTOFF
        )
        self.assertEqual(fetch_list.call_count, 1)
        self.assertEqual(self.fetch_text.call_count, 1)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual([item["title"] for item in result["items"]], ["第一条", "第二条", "第三条"])
        self.assertEqual(result["items"][0]["url"], "https://example.com/p/1.html")
        self.assertEqual(result["items"][0]["parsed"], dt.datetime(2026, 11, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertIn("startrecord=3", self.fetch_text.call_args.args[0])
        self.assertEqual(self.fetch_text.call_args.kwargs["method"], "POST")

    def test_collect_passes_form_payload_when_configured(self) -> None:
        source = _datastore_source(
            params={},
            payload={"col": "1", "unitid": "303271"},
        )
        result, _, _ = self._collect(source, FIRST_HTML, SECOND_XML, cutoff=CUTOFF)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(self.fetch_text.call_args.kwargs["payload"], {"col": "1", "unitid": "303271"})
        self.assertTrue(self.fetch_text.call_args.kwargs["form_payload"])

    def test_first_page_all_known_stops_without_dataproxy(self) -> None:
        source = _datastore_source(stop_after_known_pages=1)
        known = {"https://example.com/p/1.html", "https://example.com/p/2.html"}
        result, fetch_list, _ = self._collect(source, FIRST_HTML, SECOND_XML, known_urls=known)
        self.assertEqual(fetch_list.call_count, 1)
        self.assertEqual(self.fetch_text.call_count, 0)
        self.assertEqual(result["stop_reason"], "known_pages")

    def test_collect_passes_legacy_ssl_to_fetch_text(self) -> None:
        source = _datastore_source(legacy_ssl=True)
        result, _, _ = self._collect(source, FIRST_HTML, SECOND_XML, cutoff=CUTOFF)
        self.assertEqual(result["stop_reason"], "done")
        self.assertTrue(self.fetch_text.call_args.kwargs["legacy_renegotiation"])

    def test_cutoff_on_first_page_skips_dataproxy(self) -> None:
        old_html = FIRST_HTML.replace("2026-11-10", "2025-01-01").replace("2026-11-11", "2025-01-02")
        result, fetch_list, _ = self._collect(_datastore_source(), old_html, SECOND_XML, cutoff=CUTOFF)
        self.assertEqual(fetch_list.call_count, 1)
        self.assertEqual(self.fetch_text.call_count, 0)
        self.assertEqual(result["stop_reason"], "cutoff")
        self.assertEqual(len(result["items"]), 2)

    def test_empty_first_page_stops(self) -> None:
        result, _, _ = self._collect(
            _datastore_source(), "<html><body><p>没有记录</p></body></html>", SECOND_XML
        )
        self.assertEqual(result["stop_reason"], "empty")
        self.assertEqual(result["items"], [])


class ContainerModeTest(unittest.TestCase):
    def test_extracts_multiple_paragraphs_and_skips_scripts(self) -> None:
        result = url_extract._extract_container_content(
            CONTAINER_HTML, "https://example.com/a", "//div[@id='zoom']"
        )
        assert result is not None
        self.assertIn("第一段正文", result["content"])
        self.assertIn("第二段 加粗", result["content"])
        self.assertNotIn("alert", result["content"])
        self.assertEqual(result["images"], ["//cdn.example.com/a.jpg"])
        self.assertIn("<div id=\"zoom\">", result["body_html"])

    def test_collects_tail_and_inline_text_without_block_loss(self) -> None:
        html = (
            '<html><body><div id="zoom"><p>第一段</p>尾部文字'
            "<span>行内后缀</span><p>第二段</p></div></body></html>"
        )
        result = url_extract._extract_container_content(
            html, "https://example.com/a", "//div[@id='zoom']"
        )
        assert result is not None
        self.assertIn("第一段", result["content"])
        self.assertIn("尾部文字行内后缀", result["content"])
        self.assertIn("第二段", result["content"])

    def test_container_reapplied_after_browser_fallback(self) -> None:
        extractor = mock.Mock()
        extractor.extract.side_effect = [
            {"title": "标题", "content": "短", "body_html": "<p>短</p>", "images": []},
            {"title": "标题", "content": "短", "body_html": "<p>短</p>", "images": []},
        ]
        with mock.patch.object(url_extract, "content_needs_render", side_effect=[True, False]), \
             mock.patch.object(url_extract, "fetch_html_with_browser", return_value=CONTAINER_HTML):
            result = url_extract.extract_from_html(
                CONTAINER_HTML,
                "https://example.com/a",
                extractor,
                body_xpath="//div[@id='zoom']",
                body_mode="container",
            )
        assert result is not None
        self.assertIn("第一段正文", result["content"])
        self.assertIn("第二段 加粗", result["content"])

    def test_extract_from_html_merges_container_result(self) -> None:
        extractor = mock.Mock()
        extractor.extract.return_value = {
            "title": "标题",
            "content": "短",
            "body_html": "<p>短</p>",
            "images": [],
        }
        with mock.patch.object(url_extract, "content_needs_render", return_value=False):
            result = url_extract.extract_from_html(
                CONTAINER_HTML,
                "https://example.com/a",
                extractor,
                body_xpath="//div[@id='zoom']",
                body_mode="container",
            )
        assert result is not None
        self.assertEqual(result["title"], "标题")
        self.assertIn("第一段正文", result["content"])
        self.assertEqual(result["images"], ["https://cdn.example.com/a.jpg"])

    def test_extract_from_html_uses_container_when_gne_has_no_content(self) -> None:
        extractor = mock.Mock()
        extractor.extract.side_effect = NoContentException
        with mock.patch.object(url_extract, "content_needs_render", return_value=False):
            result = url_extract.extract_from_html(
                CONTAINER_HTML,
                "https://example.com/a",
                extractor,
                body_xpath="//div[@id='zoom']",
                body_mode="container",
            )
        assert result is not None
        self.assertIn("第一段正文", result["content"])

    def test_extract_article_forwards_body_mode(self) -> None:
        with mock.patch.object(url_extract, "fetch_html", return_value="<html></html>"), \
             mock.patch.object(url_extract, "extract_from_html", return_value={"content": "正文"}) as extract:
            url_extract.extract_article(
                "https://example.com/a", mock.Mock(), body_xpath="//div[@id='zoom']", body_mode="container"
            )
        self.assertEqual(extract.call_args.kwargs["body_mode"], "container")

    def test_load_config_validates_body_mode(self) -> None:
        def load(extra: dict) -> dict:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "sources.json"
                path.write_text(
                    json.dumps(
                        {
                            "sources": [
                                {
                                    "name": "demo",
                                    "list_url": "https://example.com/list",
                                    "item_xpath": "//li",
                                    "article_body_xpath": "//div[@id='zoom']",
                                    **extra,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return news_crawler.load_config(str(path))["sources"][0]

        self.assertEqual(load({"article_body_mode": "container"})["article_body_mode"], "container")
        self.assertEqual(load({"article_body_mode": "gne"})["article_body_mode"], "")
        self.assertEqual(load({"article_body_mode": "container", "article_body_xpath": ""})["article_body_mode"], "")

    def test_process_entry_forwards_body_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = news_crawler.init_db(str(Path(tmp) / "news.db"))
            try:
                cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
                assert cutoff is not None
                source = {
                    "name": "source",
                    "timezone": "Asia/Shanghai",
                    "article_body_xpath": "//div[@id='zoom']",
                    "article_body_mode": "container",
                }
                with mock.patch.object(
                    news_crawler, "fetch_article_html", return_value=("<html></html>", False)
                ), mock.patch.object(
                    news_crawler,
                    "extract_article",
                    return_value={
                        "title": "详情标题",
                        "content": "正文内容。" * 60,
                        "publish_time": "",
                        "author": "",
                        "images": [],
                    },
                ) as extract_mock:
                    news_crawler.process_entry(
                        conn,
                        source,
                        {"url": "https://example.com/a", "title": "列表标题", "parsed": None},
                        cutoff,
                        force=True,
                    )
                self.assertEqual(extract_mock.call_args.kwargs["body_mode"], "container")
            finally:
                conn.close()

    def test_extract_job_forwards_body_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = str(root / "news.db")
            config_file = root / "sources.json"
            config_file.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "example",
                                "timezone": "Asia/Shanghai",
                                "article_body_xpath": "//div[@id='zoom']",
                                "article_body_mode": "container",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            news_crawler.init_db(db_path).close()
            settings = Settings(
                db_path=db_path,
                config_path=str(config_file),
                timezone="Asia/Shanghai",
                allow_private_urls=True,
            )
            today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            result = {
                "title": "详情标题",
                "content": "正文内容。" * 60,
                "publish_time": today,
                "publish_time_source": "xpath",
                "title_source": "gne_default",
                "author": "",
                "images": [],
            }
            with mock.patch.object(tasks.url_extract, "extract_article", return_value=result) as extract_mock, \
                 mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/a", "source": "example"}, settings
                )
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(extract_mock.call_args.kwargs["body_mode"], "container")


if __name__ == "__main__":
    unittest.main()
