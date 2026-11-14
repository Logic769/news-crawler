"""正文 HTML 清洗的离线回归：前端 v-html 的安全边界。"""
from __future__ import annotations

import unittest

from html_sanitize import sanitize_body_html


class SanitizeBodyHtmlTest(unittest.TestCase):
    def test_keeps_semantic_structure(self) -> None:
        cleaned = sanitize_body_html(
            '<div><h2>标题</h2><p>第一段<strong>重点</strong></p></div>',
            "https://example.com/a.html",
        )
        self.assertIn("<h2>标题</h2>", cleaned)
        self.assertIn("<strong>重点</strong>", cleaned)

    def test_removes_script_and_style_subtrees(self) -> None:
        cleaned = sanitize_body_html(
            '<p>前</p><script>alert(1)</script><style>p{color:red}</style><p>后</p>'
        )
        self.assertNotIn("script", cleaned)
        self.assertNotIn("alert", cleaned)
        self.assertNotIn("color:red", cleaned)
        self.assertIn("<p>前</p>", cleaned)
        self.assertIn("<p>后</p>", cleaned)

    def test_drops_event_handlers_and_inline_style(self) -> None:
        cleaned = sanitize_body_html('<p onclick="steal()" style="color:red" class="x">正文</p>')
        self.assertEqual(cleaned, "<p>正文</p>")

    def test_only_allows_http_urls_and_resolves_relative(self) -> None:
        cleaned = sanitize_body_html(
            '<a href="javascript:alert(1)">坏链</a>'
            '<a href="/news/2.html">相对链</a>'
            '<img src="//cdn.example.com/x.jpg" alt="图">'
            '<img src="data:image/png;base64,AAAA">',
            "https://example.com/a.html",
        )
        self.assertNotIn("javascript:", cleaned)
        self.assertIn('href="https://example.com/news/2.html"', cleaned)
        self.assertIn('src="https://cdn.example.com/x.jpg"', cleaned)
        self.assertNotIn("base64", cleaned)
        self.assertEqual(cleaned.count("<img"), 1)

    def test_non_whitelisted_tag_keeps_text(self) -> None:
        cleaned = sanitize_body_html("<center><font color=red>保留文字</font></center>")
        self.assertEqual(cleaned, "保留文字")

    def test_comments_are_removed(self) -> None:
        cleaned = sanitize_body_html("<p>正文</p><!--[if IE]><script>x</script><![endif]-->")
        self.assertEqual(cleaned, "<p>正文</p>")

    def test_empty_and_markup_only_return_empty(self) -> None:
        self.assertEqual(sanitize_body_html(None), "")
        self.assertEqual(sanitize_body_html("   "), "")
        self.assertEqual(sanitize_body_html("<p>&nbsp;</p>"), "")


if __name__ == "__main__":
    unittest.main()
