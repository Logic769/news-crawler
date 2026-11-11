"""发布时间抽取回归：标签日期优先、XPath 元素节点可用、文本按文档顺序。"""
from __future__ import annotations

import unittest

from lxml.html import fromstring

from gne.extractors.time import TimeExtractor


class TimeExtractorTest(unittest.TestCase):
    def test_labeled_date_wins_over_body_time(self) -> None:
        html = (
            "<html><body>"
            "<div>会议于2026年11月13日16:00举行</div>"
            "<p>发布时间：2026年11月11日</p>"
            "</body></html>"
        )
        self.assertEqual(TimeExtractor().extract(fromstring(html)), "2026年11月11日")

    def test_user_xpath_element_node_returns_parsed_date(self) -> None:
        html = "<html><body><p class='pub'>发布时间：<span>2026-11-11</span></p></body></html>"
        self.assertEqual(
            TimeExtractor().extract(fromstring(html), publish_time_xpath="//p[@class='pub']"),
            "2026-11-11",
        )

    def test_user_xpath_without_date_falls_back(self) -> None:
        html = (
            "<html><body><p class='bad'>暂无发布时间</p>"
            "<p class='good'>2026-11-12</p></body></html>"
        )
        self.assertEqual(
            TimeExtractor().extract(fromstring(html), publish_time_xpath="//p[@class='bad']"),
            "2026-11-12",
        )

    def test_user_xpath_multiple_elements_picks_first_valid(self) -> None:
        html = (
            "<html><body><p class='pub'>无日期</p>"
            "<p class='pub'>2026-11-13</p></body></html>"
        )
        self.assertEqual(
            TimeExtractor().extract(fromstring(html), publish_time_xpath="//p[@class='pub']"),
            "2026-11-13",
        )

    def test_script_date_is_not_treated_as_visible_time(self) -> None:
        html = (
            "<html><body><p>发布时间：2012-06-24</p>"
            "<script>var d = '2012-06-24 00:00:00';</script></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2012-06-24")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_in_parent_tail_is_used(self) -> None:
        html = (
            "<html><body><p><span>发布时间：</span>2026-11-05</p>"
            "<div>会议于2026-11-06 14:00举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_in_nested_inline_tail_is_used(self) -> None:
        html = (
            "<html><body><div><div><span>发布时间：</span>2026-11-05</div></div>"
            "<div>会议于2026-11-06 14:00举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "labeled_text")

    def test_script_between_label_and_date_is_skipped(self) -> None:
        html = (
            "<html><body><div>发布时间：<script>var pub='2020-01-01';</script>"
            "2026-11-05</div><div>会议于2026-11-06 14:00举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "labeled_text")

    def test_labeled_window_stops_at_stop_words(self) -> None:
        html = (
            "<html><body><p>发布时间：详见附件 会议于2026-11-13举行</p>"
            "<p>2026-11-11</p></body></html>"
        )
        self.assertEqual(TimeExtractor().extract_from_labeled_text(fromstring(html)), "")

    def test_labeled_time_beats_date_only_meta(self) -> None:
        html = (
            "<html><head><meta itemprop='datePublished' content='2026-11-05'></head>"
            "<body><p>发布时间：2026-11-05 10:00</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05 10:00")
        self.assertEqual(source, "labeled_text")

    def test_date_update_meta_is_only_last_resort(self) -> None:
        html = (
            "<html><head><meta itemprop='dateUpdate' content='2026-11-12'></head>"
            "<body><p>发布时间：2026-11-05</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_reads_inline_span_and_beats_body_time(self) -> None:
        html = (
            "<html><body><p>发布时间：<span>2026-11-05</span></p>"
            "<div>会议于2026-11-06 14:00举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "labeled_text")

    def test_labeled_window_does_not_cross_block_elements(self) -> None:
        html = (
            "<html><body><p>发布时间：</p><p>2026-11-11</p>"
            "<script>var old = '2020-01-01';</script></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "text")

    def test_user_xpath_string_scalar_is_used(self) -> None:
        html = "<html><body><p class='pub'>2026-11-11</p></body></html>"
        self.assertEqual(
            TimeExtractor().extract(
                fromstring(html), publish_time_xpath="normalize-space(//p[@class='pub'])"
            ),
            "2026-11-11",
        )

    def test_invalid_user_xpath_is_ignored(self) -> None:
        html = "<html><body><p>2026-11-11</p></body></html>"
        self.assertEqual(
            TimeExtractor().extract(fromstring(html), publish_time_xpath="//p["),
            "2026-11-11",
        )

    def test_labeled_date_in_sibling_span_is_used(self) -> None:
        html = (
            "<html><body><div class='meta'>"
            "<span>发布日期：2016-11-22</span><span>浏览次数：1</span>"
            "</div><div>正文提到 2020-01-01</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2016-11-22")
        self.assertEqual(source, "labeled_text")

    def test_labeled_window_does_not_cross_block_when_label_has_no_date(self) -> None:
        html = (
            "<html><body><p>发布时间：</p><p>2026-11-11</p>"
            "<script>var old = '2020-01-01';</script></body></html>"
        )
        self.assertEqual(TimeExtractor().extract_from_labeled_text(fromstring(html)), "")

    def test_publish_label_beats_update_label(self) -> None:
        html = (
            "<html><body><p>更新时间：2026-11-12</p>"
            "<p>发布时间：2026-11-11</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "labeled_text")

    def test_labeled_with_time_skips_full_text_scan(self) -> None:
        html = (
            "<html><body><p>发布时间：2026-11-11 16:00:00</p>"
            "<div>会议时间 2020-01-01 10:00:00</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11 16:00:00")
        self.assertEqual(source, "labeled_text")

    def test_split_year_span_date_is_rebuilt(self) -> None:
        html = "<html><body><p class='pub'>2019<span>-09-30 22:46:13</span></p></body></html>"
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2019-09-30 22:46:13")
        self.assertEqual(source, "text")

    def test_split_chinese_year_span_date_is_rebuilt(self) -> None:
        html = "<html><body><p class='pub'>2019<span>年09月30日</span></p></body></html>"
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2019年09月30日")
        self.assertEqual(source, "text")

    def test_split_date_is_not_joined_across_blocks(self) -> None:
        html = (
            "<html><body><p>2019</p><p>-09-30 22:46:13</p></body></html>"
        )
        self.assertEqual(TimeExtractor().extract_from_text(fromstring(html)), "")

    def test_labeled_date_only_uses_same_date_meta_time(self) -> None:
        html = (
            "<html><head><meta itemprop='datePublished' content='2026-11-05 10:00'></head>"
            "<body><p>发布时间：2026-11-05</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05 10:00")
        self.assertEqual(source, "meta")

    def test_footer_labeled_date_is_ignored(self) -> None:
        html = (
            "<html><body><p>2026-11-05</p>"
            "<footer>发布时间：2020-01-01</footer></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-05")
        self.assertEqual(source, "text")

    def test_user_xpath_wins_over_labeled_text(self) -> None:
        html = (
            "<html><body><p>发布时间：2026-11-05</p>"
            "<p class='pub'>2026-11-11</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(
            fromstring(html), publish_time_xpath="//p[@class='pub']"
        )
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "xpath")

    def test_first_published_meta_dash_time_is_normalized(self) -> None:
        html = (
            "<html><head><meta name='firstpublishedtime' "
            "content='2007-08-20-23:59:59'></head><body><p>正文</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2007-08-20 23:59:59")
        self.assertEqual(source, "meta")

    def test_user_xpath_prefers_earlier_date_over_later_iso(self) -> None:
        # 同一段文本里同时有发布时间与较晚的更新时间时，取文本位置更早的日期。
        html = (
            "<html><body><p class='pub'>发布时间：2026-11-11 "
            "更新时间：2026-11-12T09:30:00+08:00</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(
            fromstring(html), publish_time_xpath="//p[@class='pub']"
        )
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "xpath")

    def test_user_xpath_iso_datetime_keeps_time_and_offset(self) -> None:
        html = "<html><body><p class='pub'>2026-11-10T23:30:00Z</p></body></html>"
        value, source = TimeExtractor().extract_with_source(
            fromstring(html), publish_time_xpath="//p[@class='pub']"
        )
        self.assertEqual(value, "2026-11-10T23:30:00Z")
        self.assertEqual(source, "xpath")

    def test_user_xpath_skips_invalid_iso_candidate(self) -> None:
        html = (
            "<html><body><p class='pub'>2026-13-01T10:00</p>"
            "<p class='pub'>2026-11-11T09:30:00+08:00</p></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(
            fromstring(html), publish_time_xpath="//p[@class='pub']/text()"
        )
        self.assertEqual(value, "2026-11-11T09:30:00+08:00")
        self.assertEqual(source, "xpath")

    def test_adjacent_sibling_script_date_is_skipped(self) -> None:
        html = (
            "<html><body><div class='meta'><span>发布时间：</span>"
            "<span><script>var d='2010-01-01';</script>2026-11-11</span></div>"
            "<div>会议于2020-01-01举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_does_not_cross_block_boundary(self) -> None:
        # 标签单独占一个块时，下一个块里的日期属于正文，不能当作发布时间。
        html = (
            "<html><body><div>发布时间：</div>"
            "<div>2026-11-11</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11")
        self.assertEqual(source, "text")

    def test_labeled_date_in_separate_adjacent_spans(self) -> None:
        html = (
            "<html><body><div class='meta'>"
            "<span>发布时间：</span><span>2026-11-11 09:30</span></div>"
            "<div>会议于2020-01-01举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11 09:30")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_in_table_cells(self) -> None:
        # 表格布局常见“<td>发布时间：</td><td>2026-11-11 09:30</td>”，
        # 单元格是行内容器，不应被当成块级边界切断。
        html = (
            "<html><body><table><tr>"
            "<td>发布时间：</td><td>2026-11-11 09:30</td>"
            "</tr></table><div>会议于2020-01-01举行</div></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11 09:30")
        self.assertEqual(source, "labeled_text")

    def test_labeled_date_does_not_cross_table_rows(self) -> None:
        # td 不再是块级边界后，必须保证不会跨 <tr> 把下一行日期当成发布时间。
        html = (
            "<html><body><table>"
            "<tr><td>发布时间：</td></tr>"
            "<tr><td>2026-11-11 09:30</td></tr>"
            "</table></body></html>"
        )
        value, source = TimeExtractor().extract_with_source(fromstring(html))
        self.assertEqual(value, "2026-11-11 09:30")
        self.assertEqual(source, "text")


if __name__ == "__main__":
    unittest.main()
