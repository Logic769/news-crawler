"""article_body_xpath 透传回归。

背景：stcsm_xmsh 这类政府站点的正文在 <div id="ivs_content"> 内，GNE 默认
密度算法会被页面其他长文本块带偏（实测只抽到 47 字标题），指定 body_xpath
后能抽到 447 字正文。因此需要把站点配置的 article_body_xpath 一路透传到
GNE 的 body_xpath，覆盖 CLI 抓取与 API extract 作业两条入口。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from lxml.html import HtmlElement

import news_crawler
import url_extract
from gne import GeneralNewsExtractor
from news_api import tasks
from news_api.settings import Settings

# 默认抽取会被长文本块带偏，只有指定 body_xpath 才能命中真正文。
DECOY_HTML = (
    "<html><body>"
    "<div id='real'><p>各有关单位：请按要求填报申报书。</p></div>"
    "<div id='decoy'><p>" + "无关的推荐阅读摘要文字。" * 30 + "</p></div>"
    "</body></html>"
)


class BodyXPathExtractionTest(unittest.TestCase):
    def test_default_extraction_is_led_astray(self) -> None:
        # 记录基线行为：不传 body_xpath 时抽到的是诱饵文本。
        result = GeneralNewsExtractor().extract(DECOY_HTML)
        self.assertIn("推荐阅读", result["content"])
        self.assertNotIn("各有关单位", result["content"])

    def test_body_xpath_selects_real_content(self) -> None:
        result = GeneralNewsExtractor().extract(
            DECOY_HTML, body_xpath="//div[@id='real']"
        )
        self.assertIn("各有关单位", result["content"])
        self.assertNotIn("推荐阅读", result["content"])

    def test_unmatched_body_xpath_falls_back_to_body(self) -> None:
        # ContentExtractor 对未命中的 body_xpath 回退 //body：正文应等于默认
        # 抽取结果（诱饵），且留一条告警——只断言「非空」是假覆盖。
        with self.assertLogs("gne.extractors.content", level="WARNING") as captured:
            result = GeneralNewsExtractor().extract(
                DECOY_HTML, body_xpath="//div[@id='nope']"
            )
        default = GeneralNewsExtractor().extract(DECOY_HTML)
        self.assertEqual(result["content"], default["content"])
        self.assertIn("推荐阅读", result["content"])
        self.assertTrue(any("未命中任何节点" in line for line in captured.output))

    def test_multiple_matches_use_first_and_warn(self) -> None:
        # 宽松表达式命中多个容器时仍取第一个，但必须告警，否则侧栏会被静默
        # 当成正文以 status=ok 入库。
        html = (
            "<html><body>"
            "<div class='content'><p>侧栏导航文字</p></div>"
            "<div class='content'><p>" + "各有关单位请按要求填报申报书并提交材料。" * 8 + "</p></div>"
            "</body></html>"
        )
        with self.assertLogs("gne.extractors.content", level="WARNING") as captured:
            result = GeneralNewsExtractor().extract(
                html, body_xpath="//div[contains(@class,'content')]"
            )
        self.assertIn("侧栏导航文字", result["content"])
        self.assertTrue(
            any("命中 2 个节点" in line for line in captured.output), captured.output
        )

    def test_extract_from_html_passes_body_xpath_to_extractor(self) -> None:
        extractor = mock.Mock()
        extractor.extract.return_value = {
            "title": "标题",
            "content": "正文内容。" * 60,
            "images": [],
        }
        url_extract.extract_from_html(
            "<html></html>",
            "https://example.com/a",
            extractor,
            body_xpath="//div[@id='real']",
        )
        self.assertEqual(
            extractor.extract.call_args.kwargs["body_xpath"], "//div[@id='real']"
        )

    def test_extract_from_html_without_body_xpath_passes_empty(self) -> None:
        extractor = mock.Mock()
        extractor.extract.return_value = {
            "title": "标题",
            "content": "正文内容。" * 60,
            "images": [],
        }
        url_extract.extract_from_html(
            "<html></html>", "https://example.com/a", extractor
        )
        self.assertEqual(extractor.extract.call_args.kwargs["body_xpath"], "")


class BodyXPathConfigTest(unittest.TestCase):
    def _load(self, source: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps({"sources": [source]}, ensure_ascii=False),
                encoding="utf-8",
            )
            return news_crawler.load_config(str(path))["sources"][0]

    def test_valid_body_xpath_is_kept(self) -> None:
        source = self._load(
            {
                "name": "demo",
                "list_url": "https://example.com/list",
                "item_xpath": "//li",
                "article_body_xpath": "//div[@id='ivs_content']",
            }
        )
        self.assertEqual(source["article_body_xpath"], "//div[@id='ivs_content']")

    def test_invalid_body_xpath_is_ignored(self) -> None:
        with self.assertLogs("news_crawler", level="WARNING") as captured:
            source = self._load(
                {
                    "name": "demo",
                    "list_url": "https://example.com/list",
                    "item_xpath": "//li",
                    "article_body_xpath": "//div[",
                }
            )
        self.assertEqual(source["article_body_xpath"], "")
        self.assertTrue(
            any("article_body_xpath" in line for line in captured.output)
        )

    def test_scalar_body_xpath_is_ignored(self) -> None:
        with self.assertLogs("news_crawler", level="WARNING"):
            source = self._load(
                {
                    "name": "demo",
                    "list_url": "https://example.com/list",
                    "item_xpath": "//li",
                    "article_body_xpath": "string(//div[@id='x'])",
                }
            )
        self.assertEqual(source["article_body_xpath"], "")


class BodyXPathProcessEntryTest(unittest.TestCase):
    def test_process_entry_forwards_body_xpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = news_crawler.init_db(str(Path(tmp) / "news.db"))
            try:
                cutoff = news_crawler.parse_datetime("2026-01-01", None, "Asia/Shanghai")
                assert cutoff is not None
                source = {
                    "name": "source",
                    "timezone": "Asia/Shanghai",
                    "article_body_xpath": "//div[@id='ivs_content']",
                }
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    return_value=("<html></html>", False),
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
                        {
                            "url": "https://example.com/a",
                            "title": "列表标题",
                            "parsed": None,
                        },
                        cutoff,
                        force=True,
                    )
                self.assertEqual(
                    extract_mock.call_args.kwargs["body_xpath"],
                    "//div[@id='ivs_content']",
                )
            finally:
                conn.close()


class BodyXPathExtractJobTest(unittest.TestCase):
    def test_extract_job_forwards_body_xpath(self) -> None:
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
                                "article_body_xpath": "//div[@id='ivs_content']",
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
            with mock.patch.object(
                tasks.url_extract, "extract_article", return_value=result
            ) as extract_mock, mock.patch.object(tasks, "close_thread_browser"):
                summary = tasks.execute_extract(
                    {"url": "https://example.com/a", "source": "example"}, settings
                )
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(
                extract_mock.call_args.kwargs["body_xpath"],
                "//div[@id='ivs_content']",
            )


# 真正文足够长（>100 字），避免触发 content_needs_render 的渲染回退；
# 诱饵更长，确保默认密度算法一定会被带偏。
LONG_HTML = (
    "<html><body>"
    "<div id='real'><p>" + "各有关单位：请按要求填报申报书。" * 15 + "</p></div>"
    "<div id='decoy'><p>" + "无关的推荐阅读摘要文字。" * 40 + "</p></div>"
    "</body></html>"
)


class BodyXPathWiringTest(unittest.TestCase):
    """锁住中间两层透传：不 mock 被测层，只 mock 网络与更下层。"""

    def test_crawler_extract_article_forwards_to_extract_from_html(self) -> None:
        with mock.patch.object(
            news_crawler, "extract_from_html", return_value={"content": "正文"}
        ) as from_html:
            news_crawler.extract_article(
                "<html></html>",
                "https://example.com/a",
                body_xpath="//div[@id='real']",
            )
        self.assertEqual(from_html.call_args.kwargs["body_xpath"], "//div[@id='real']")

    def test_url_extract_article_end_to_end_uses_body_xpath(self) -> None:
        # 真实 extract_article + 真实 GNE：body_xpath 断了就抽到诱饵。
        with mock.patch.object(url_extract, "fetch_html", return_value=LONG_HTML):
            result = url_extract.extract_article(
                "https://example.com/a",
                GeneralNewsExtractor(),
                allow_browser=False,
                body_xpath="//div[@id='real']",
            )
        assert result is not None
        self.assertIn("各有关单位", result["content"])
        self.assertNotIn("推荐阅读", result["content"])

    def test_browser_fallback_second_extract_uses_body_xpath(self) -> None:
        # 第一次抓到的 HTML 没有正文 → 触发渲染回退 → 第二次 extract 必须
        # 同样带 body_xpath，否则渲染后仍抽到诱饵（这条锁 url_extract.py 内
        # 浏览器回退分支的透传）。
        empty_shell = "<html><body><h1>标题</h1></body></html>"
        with mock.patch.object(
            url_extract, "fetch_html", return_value=empty_shell
        ), mock.patch.object(
            url_extract, "fetch_html_with_browser", return_value=LONG_HTML
        ) as browser:
            result = url_extract.extract_article(
                "https://example.com/a",
                GeneralNewsExtractor(),
                body_xpath="//div[@id='real']",
            )
        self.assertEqual(browser.call_count, 1)
        assert result is not None
        self.assertIn("各有关单位", result["content"])
        self.assertNotIn("推荐阅读", result["content"])


class ResolveBodyTest(unittest.TestCase):
    """_resolve_body：非元素/未命中/空容器一律回退 //body，不抛异常。"""

    def _resolve(self, body_xpath: str) -> HtmlElement:
        from gne.extractors.content import ContentExtractor
        from gne.html import html2element

        element = html2element(
            "<html><body>"
            "<div id='real'><p>真正文</p></div>"
            "<div id='empty'></div>"
            "</body></html>"
        )
        with self.assertLogs("gne.extractors.content", level="WARNING") as captured:
            node = ContentExtractor._resolve_body(element, body_xpath)
        self.assertTrue(any("body_xpath" in line for line in captured.output))
        return node

    def test_attribute_expression_falls_back(self) -> None:
        node = self._resolve("//div/@id")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_text_expression_falls_back(self) -> None:
        node = self._resolve("//div[@id='real']/text()")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_unmatched_xpath_falls_back(self) -> None:
        node = self._resolve("//div[@id='nope']")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_empty_container_falls_back(self) -> None:
        node = self._resolve("//div[@id='empty']")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_boolean_expression_falls_back(self) -> None:
        # true() 不以 count(/boolean( 等前缀开头，配置校验不会拦下，
        # lxml 返回 bool，直接下标会抛 TypeError 并穿透到 extract。
        node = self._resolve("true()")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_numeric_expression_falls_back(self) -> None:
        node = self._resolve("1 + count(//div)")
        self.assertIsInstance(node, HtmlElement)
        self.assertEqual(node.tag, "body")

    def test_matched_container_is_used(self) -> None:
        from gne.extractors.content import ContentExtractor
        from gne.html import html2element

        element = html2element("<html><body><div id='real'><p>真正文</p></div></body></html>")
        node = ContentExtractor._resolve_body(element, "//div[@id='real']")
        self.assertEqual(node.get("id"), "real")


class BackfillBodyXPathTest(unittest.TestCase):
    """回填脚本也要读站点配置的 article_body_xpath。"""

    def test_load_source_xpaths_reads_config(self) -> None:
        import backfill_raw_content as bf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "site",
                                "list_url": "https://example.com/list",
                                "item_xpath": "//li",
                                "article_body_xpath": "//div[@id='ivs_content']",
                            },
                            {
                                "name": "other",
                                "list_url": "https://example.com/list2",
                                "item_xpath": "//li",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                bf.load_source_xpaths(str(path)),
                {"site": "//div[@id='ivs_content']"},
            )

    def test_load_source_xpaths_missing_config_returns_empty(self) -> None:
        import backfill_raw_content as bf

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bf.load_source_xpaths(str(Path(tmp) / "nope.json")), {})

    def test_backfill_forwards_body_xpath(self) -> None:
        import backfill_raw_content as bf

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            conn = news_crawler.init_db(db_path)
            try:
                conn.execute(
                    "INSERT INTO articles (source, url, title, content, raw_content, status) "
                    "VALUES ('site', 'https://example.com/a', '标题', '正文', NULL, 'ok')"
                )
                conn.commit()
                with mock.patch.object(
                    bf,
                    "extract_article",
                    return_value={"content": "回填正文"},
                ) as extract_mock, mock.patch.object(bf, "close_thread_browser"):
                    updated, failed, _, _, _ = bf.backfill(
                        conn,
                        [("site", "https://example.com/a")],
                        delay=0,
                        allow_browser=False,
                        dry_run=False,
                        source_xpaths={"site": "//div[@id='ivs_content']"},
                    )
                self.assertEqual((updated, failed), (1, 0))
                self.assertEqual(
                    extract_mock.call_args.kwargs["body_xpath"],
                    "//div[@id='ivs_content']",
                )
            finally:
                conn.close()

    def test_load_source_xpaths_non_dict_toplevel_returns_empty(self) -> None:
        # 顶层写成字符串/数字时 load_config 抛 ValueError（不是 AttributeError），
        # 由这里捕获成空字典，避免回填崩栈。
        import backfill_raw_content as bf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text('"hello"', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(bf.load_source_xpaths(str(path)), {})
            self.assertIn("顶层必须是对象或数组", err.getvalue())

    def test_load_config_rejects_non_dict_toplevel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text("42", encoding="utf-8")
            with self.assertRaises(ValueError):
                news_crawler.load_config(str(path))

    def test_main_warns_when_no_body_xpath_found(self) -> None:
        # 未读到任何 article_body_xpath 时必须提示，否则用户会以为配置已生效。
        import backfill_raw_content as bf

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            cfg = Path(tmp) / "sources.json"
            cfg.write_text(
                json.dumps(
                    {"sources": [{"name": "site", "list_url": "https://example.com/l", "item_xpath": "//li"}]}
                ),
                encoding="utf-8",
            )
            news_crawler.init_db(db_path).close()
            argv = ["backfill_raw_content.py", "--db", db_path, "--config", str(cfg)]
            with mock.patch.object(bf.sys, "argv", argv), mock.patch.object(
                bf, "load_source_xpaths", wraps=bf.load_source_xpaths
            ), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(bf.main(), 0)
            self.assertIn("未在", out.getvalue())
            self.assertIn("article_body_xpath", out.getvalue())


if __name__ == "__main__":
    unittest.main()
