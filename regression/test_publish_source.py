"""发布来源标记回归：内部抽取来源映射、视频页来源与标题清洗。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import news_crawler
from url_extract import extract_video_meta


class PublishSourceTest(unittest.TestCase):
    def test_internal_sources_map_to_stored_values(self) -> None:
        cases = {
            "xpath": "xpath",
            "labeled_text": "labeled_text",
            "meta": "meta",
            "json_ld": "meta",
            "time_tag": "meta",
            "text": "text",
            "video_meta": "video_meta",
            "unexpected": "detail",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.publish_source_for(raw), expected)

    def test_video_meta_reports_video_source_when_date_found(self) -> None:
        html = (
            "<html><head>"
            "<meta property='og:title' content='视频标题'>"
            "<meta property='article:published_time' content='2026-11-11T10:00:00+08:00'>"
            "</head><body></body></html>"
        )
        result = extract_video_meta(html, "https://example.com/video/1")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["publish_time_source"], "video_meta")
        self.assertEqual(result["publish_time"], "2026-11-11")

    def test_clean_title_strips_literal_html_tags(self) -> None:
        cases = {
            # 站点把标题写进 title 属性，属性值里的 <br> 不会被浏览器解析，
            # 会被原样抽出来；入库前统一清掉，且不留空格断词。
            "填报<br>正式申报书的通知": "填报正式申报书的通知",
            "关于<BR/>开展<b>申报</b>的通知": "关于开展申报的通知",
            "  <p>多行</p> 标题 ": "多行 标题",
            "正常标题，含 a < b > c 比较": "正常标题，含 a < b > c 比较",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.clean_title(raw), expected)

    def test_clean_title_strips_tags_with_long_attributes(self) -> None:
        # 属性窗口不设上限：带长 style 的标签也要整段删掉，否则会残留半截开标签
        # （开标签因属性过长不匹配、闭标签被删，标题里留下 '<span style="...">'）。
        cases = {
            '<span style="font-family:Microsoft YaHei;color:#333">标题</span>': "标题",
            '<p style="margin:0;padding:0;line-height:1.8;text-align:justify">正文</p>': "正文",
            "<b style='font-weight:bold;color:red'>加粗</b>通知": "加粗通知",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.clean_title(raw), expected)

    def test_clean_title_leaves_plain_text_untouched(self) -> None:
        text = "关于组织申报2026年度国家重点研发计划项目的通知"
        self.assertEqual(news_crawler.clean_title(text), text)

    def test_clean_title_keeps_letter_angle_brackets_used_as_math(self) -> None:
        # 单字母标签名与比较式同形，只有带空格的自然写法才保留：
        # <B> 前后都有空格时不删，避免把「满足 A <B> C 条件」删成「满足 A C 条件」。
        cases = {
            "满足 A <B> C 条件": "满足 A <B> C 条件",
            "支持 x <i> y 的写法": "支持 x <i> y 的写法",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.clean_title(raw), expected)

    def test_clean_title_strips_letter_tags_in_unambiguous_forms(self) -> None:
        # 成对无属性、带属性、自闭合、孤立结束标签这几种写法不会出现在正文里，按标签清除。
        cases = {
            "<b>加粗</b>通知": "加粗通知",
            "<b class='x'>加粗</b>通知": "加粗通知",
            "通知</b>": "通知",
            "通知<u/>": "通知",
            "无 <a href='/x'>链接</a> 的标题": "无 链接 的标题",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(news_crawler.clean_title(raw), expected)


class CleanTitleWiringTest(unittest.TestCase):
    """clean_title 必须真的接在列表解析与详情入库路径上，而不只是纯函数可用。"""

    def test_parse_list_items_cleans_attribute_title(self) -> None:
        html = (
            "<html><body><ul class='news-list'>"
            "<li><div title='填报<br>正式申报书的通知'></div>"
            "<a href='/a.html'>填报<br>正式申报书的通知</a></li>"
            "</ul></body></html>"
        )
        source = {
            "name": "s",
            "item_xpath": "//ul[contains(@class,'news-list')]/li",
            "title_xpath": ".//div/@title",
            "url_xpath": ".//a/@href",
        }
        entries = news_crawler.parse_list_items(html, source, "https://example.com/")
        self.assertEqual(entries[0]["title"], "填报正式申报书的通知")

    def test_process_entry_cleans_detail_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            news_crawler.init_db(db_path).close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                cutoff = news_crawler.parse_datetime(
                    "2026-01-01", None, "Asia/Shanghai"
                )
                assert cutoff is not None
                result = {
                    "title": "填报<br>正式申报书的通知",
                    "content": "正文内容。" * 80,
                    "publish_time": "2026-11-05",
                    "title_source": "gne_default",
                    "publish_time_source": "xpath",
                    "author": "",
                    "images": [],
                }
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    return_value=("<html></html>", False),
                ), mock.patch.object(
                    news_crawler, "extract_article", return_value=result
                ):
                    status = news_crawler.process_entry(
                        conn,
                        {"name": "s", "timezone": "Asia/Shanghai"},
                        {
                            "url": "https://example.com/a",
                            "title": "列表标题",
                            "parsed": None,
                        },
                        cutoff,
                        force=True,
                    )
                self.assertEqual(status, "ok")
                row = conn.execute("SELECT title FROM articles").fetchone()
                self.assertEqual(row["title"], "填报正式申报书的通知")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
