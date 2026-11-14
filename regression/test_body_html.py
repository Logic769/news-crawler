"""正文 HTML 序列化的实体保真测试（不联网）。"""

from __future__ import annotations

import unittest

from gne import GeneralNewsExtractor


class BodyHtmlEntitiesTest(unittest.TestCase):
    def test_body_html_keeps_text_entities_instead_of_unmarshalling_tags(self) -> None:
        html = """<html><body><div class="content">
        <p>第一段：关于项目申报的说明，内容需要足够长以触发正文抽取算法。</p>
        <p>代码示例：&lt;div class=x&gt;内容&lt;/div&gt; 以及当 a &lt;b 时，成本 &gt; 收入。</p>
        <p>脚本示例：&lt;script&gt;alert(1)&lt;/script&gt; 不应被当成真标签。</p>
        <p>第三段：申报截止时间、材料清单和联系方式详见附件，请按要求提交。</p>
        </div></body></html>"""
        result = GeneralNewsExtractor().extract(html, with_body_html=True)
        body_html = str(result.get("body_html") or "")
        self.assertIn("&lt;div class=x&gt;内容&lt;/div&gt;", body_html)
        self.assertIn("当 a &lt;b 时，成本 &gt; 收入", body_html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body_html)
        self.assertNotIn("<div class=x>", body_html)
        self.assertNotIn("<script>", body_html)


if __name__ == "__main__":
    unittest.main()
