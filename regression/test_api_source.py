"""接口型源回归：JSON 接口翻页采集、正文随条目入库、配置归一。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import news_crawler

CUTOFF = datetime(2026, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai"))


def _item(index: int, *, date: str = "2026-11-10 12:00:00", body: str = "<p>正文段落</p>") -> dict:
    return {
        "id": f"ZZ{index}",
        "name": f"政策 {index}",
        "publishTime": date,
        "originalURL": f"https://example.com/p/{index}.html",
        "content": body,
    }


def _api_source(**api) -> dict:
    base_api = {
        "url": "https://example.com/api/list",
        "method": "POST",
        "payload": {"area": "上海市"},
        "items_path": "data.respData.dataList",
        "total_path": "data.respData.total",
        "page_size": 2,
        "max_pages": 3,
    }
    base_api.update(api)
    return {
        "name": "shpolicy",
        "timezone": "Asia/Shanghai",
        "delay": 0,
        "api": base_api,
    }


def _page(*items: dict) -> dict:
    return {"data": {"respData": {"total": 100, "dataList": list(items)}}}


class ApiCollectTest(unittest.TestCase):
    """接口翻页：条目、正文、终止条件。"""

    def _collect(self, source: dict, pages: list[dict], **kwargs):
        calls: list[tuple[str, dict | None]] = []
        self.validators: list = []

        def fake_fetch(
            url, *, method="GET", payload=None, headers=None, url_validator=None
        ):
            calls.append((url, payload))
            self.validators.append(url_validator)
            if len(calls) > len(pages):
                raise AssertionError("请求页数超出预期")
            return pages[len(calls) - 1]

        with mock.patch("url_extract.fetch_json", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source, **kwargs)
        return result, calls

    def test_items_carry_inline_body_and_dates(self) -> None:
        # page_size 大于本页条数：不足一页即最后一页，只请求一次。
        source = _api_source(page_size=5)
        result, calls = self._collect(
            source,
            [_page(_item(1), _item(2))],
            cutoff=CUTOFF,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["pageNum"], 1)
        self.assertEqual(calls[0][1]["pageSize"], 5)
        self.assertEqual(calls[0][1]["area"], "上海市")
        self.assertEqual(len(result["items"]), 2)
        entry = result["items"][0]
        self.assertEqual(entry["title"], "政策 1")
        self.assertEqual(entry["url"], "https://example.com/p/1.html")
        self.assertEqual(entry["api_id"], "ZZ1")
        self.assertEqual(entry["raw_content"], "正文段落")
        self.assertEqual(entry["content"], "正文段落")
        self.assertEqual(
            entry["parsed"], datetime(2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        )

    def test_stops_when_total_reached(self) -> None:
        source = _api_source(page_size=1, max_pages=5)
        first = _page(_item(1))
        first["data"]["respData"]["total"] = 1
        result, calls = self._collect(source, [first])

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(result["pages"], 1)

    def test_stops_on_short_page(self) -> None:
        source = _api_source(page_size=5, max_pages=5)
        result, calls = self._collect(source, [_page(_item(1), _item(2))])

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(len(result["items"]), 2)

    def test_cutoff_stops_further_pages(self) -> None:
        source = _api_source(page_size=2, max_pages=5)
        result, calls = self._collect(
            source,
            [_page(_item(1, date="2025-01-01 00:00:00"), _item(2, date="2025-01-02 00:00:00"))],
            cutoff=CUTOFF,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stop_reason"], "cutoff")
        self.assertEqual(len(result["items"]), 2)

    def test_known_pages_stop_after_configured_streak(self) -> None:
        source = _api_source(page_size=1, max_pages=5, stop_after_known_pages=2)
        pages = [_page(_item(1)), _page(_item(2)), _page(_item(3))]
        known = {"https://example.com/p/1.html", "https://example.com/p/2.html"}
        result, calls = self._collect(source, pages, known_urls=known)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["stop_reason"], "known_pages")

    def test_fetch_failure_keeps_stop_reason(self) -> None:
        source = _api_source()

        def fake_fetch(*args, **kwargs):
            raise OSError("connection reset")

        with mock.patch("url_extract.fetch_json", side_effect=fake_fetch):
            result = news_crawler.collect_list_entries_detailed(source, cutoff=CUTOFF)

        self.assertEqual(result["stop_reason"], "fetch_failed")
        self.assertEqual(result["items"], [])

    def test_non_list_items_path_stops(self) -> None:
        source = _api_source()
        result, calls = self._collect(source, [{"data": {"respData": {"dataList": "oops"}}}])

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stop_reason"], "empty")
        self.assertEqual(result["items"], [])

    def test_url_template_used_when_url_field_missing(self) -> None:
        source = _api_source(url_field="", url_template="https://example.com/detail/{id}")
        item = _item(7)
        item.pop("originalURL")
        result, _ = self._collect(source, [_page(item)])

        self.assertEqual(result["items"][0]["url"], "https://example.com/detail/ZZ7")

    def test_item_without_url_is_skipped(self) -> None:
        source = _api_source(url_field="")
        item = _item(1)
        item.pop("originalURL")
        result, _ = self._collect(source, [_page(item)])

        self.assertEqual(result["items"], [])

    def test_duplicate_urls_across_pages_are_dropped(self) -> None:
        source = _api_source(page_size=2, max_pages=5)
        result, calls = self._collect(
            source,
            [
                _page(_item(1), _item(2)),
                _page(_item(2), _item(3)),
                # 最后一页只有 1 条（少于 page_size），翻页到此结束。
                _page(_item(4)),
            ],
        )

        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [entry["url"] for entry in result["items"]],
            [
                "https://example.com/p/1.html",
                "https://example.com/p/2.html",
                "https://example.com/p/3.html",
                "https://example.com/p/4.html",
            ],
        )

    def test_max_pages_reached_sets_stop_reason(self) -> None:
        # 接口不报 total、每页都是满页：只能靠 max_pages 截断，
        # 此时 stop_reason 必须是 max_pages，不能停在初值 done。
        source = _api_source(page_size=1, max_pages=3, total_path="")
        result, calls = self._collect(
            source,
            [_page(_item(1)), _page(_item(2)), _page(_item(3))],
        )

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["stop_reason"], "max_pages")

    def test_delay_sleeps_between_pages(self) -> None:
        source = _api_source(page_size=1, max_pages=5)
        source["delay"] = 0.7
        pages = [_page(_item(1)), _page(_item(2)), _page(_item(3))]
        # 接口报告总数用尽：第 3 页后结束，不再等待。
        for page in pages:
            page["data"]["respData"]["total"] = 3
        with mock.patch.object(news_crawler.time, "sleep") as slept:
            result, calls = self._collect(source, pages)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual([call.args[0] for call in slept.call_args_list], [0.7, 0.7])

    def test_delay_does_not_sleep_after_last_page(self) -> None:
        # max_pages 自然走完时没有"下一页"，不该再白等一次 delay。
        source = _api_source(page_size=1, max_pages=3, total_path="")
        source["delay"] = 0.7
        pages = [_page(_item(1)), _page(_item(2)), _page(_item(3))]
        with mock.patch.object(news_crawler.time, "sleep") as slept:
            result, calls = self._collect(source, pages)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["stop_reason"], "max_pages")
        self.assertEqual([call.args[0] for call in slept.call_args_list], [0.7, 0.7])

    def test_get_method_sends_no_payload(self) -> None:
        source = _api_source(method="GET", page_size=5)
        source["api"]["url"] = "https://example.com/api/list?page={page}"
        result, calls = self._collect(source, [_page(_item(1))])

        self.assertEqual(len(calls), 1)
        # GET 的页码只能靠 url 模板，请求体必须为空。
        self.assertIsNone(calls[0][1])
        self.assertEqual(calls[0][0], "https://example.com/api/list?page=1")
        self.assertEqual(len(result["items"]), 1)

    def test_project_mode_does_not_stop_on_cutoff(self) -> None:
        # 项目申报页的日期是公告发布日期，整页都在窗口前不代表后续没有
        # 仍在受理的项目，禁止据此早停。
        source = _api_source(page_size=2, max_pages=5)
        source["listing_type"] = "project_application"
        result, calls = self._collect(
            source,
            [
                _page(
                    _item(1, date="2025-01-01 00:00:00"),
                    _item(2, date="2025-01-02 00:00:00"),
                ),
                _page(_item(3)),
            ],
            cutoff=CUTOFF,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(len(result["items"]), 3)

    def test_relative_url_is_completed_with_api_origin(self) -> None:
        # 接口常返回 "/p/1.html"：没有列表页可锚定，必须用 api.url 的
        # origin 补全，否则入库与详情抓取都是不可访问的相对串。
        source = _api_source(page_size=5)
        item = _item(1)
        item["originalURL"] = "/p/1.html"
        result, _ = self._collect(source, [_page(item)])

        self.assertEqual(result["items"][0]["url"], "https://example.com/p/1.html")

    def test_explicit_base_url_overrides_api_origin(self) -> None:
        # 接口与详情页不同域时由 base_url 覆盖，不能被 api.url 抢走。
        source = _api_source(page_size=5, base_url="https://detail.example.org/news/")
        item = _item(1)
        item["originalURL"] = "p/1.html"
        result, _ = self._collect(source, [_page(item)])

        self.assertEqual(
            result["items"][0]["url"], "https://detail.example.org/news/p/1.html"
        )

    def test_known_pages_uses_deduped_page_items(self) -> None:
        # 第 2 页是「重复的 A + 已入库的 B」：去重后只剩已入库的 B，
        # 若拿去重结果判早停会误判整页已入库而漏掉后面的页。
        source = _api_source(page_size=2, max_pages=5, stop_after_known_pages=1)
        pages = [
            _page(_item(1), _item(9)),
            _page(_item(1), _item(2)),
            _page(_item(3)),
        ]
        known = {"https://example.com/p/2.html"}
        result, calls = self._collect(source, pages, known_urls=known)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(
            [entry["url"] for entry in result["items"]],
            [
                "https://example.com/p/1.html",
                "https://example.com/p/9.html",
                "https://example.com/p/2.html",
                "https://example.com/p/3.html",
            ],
        )

    def test_cutoff_uses_deduped_page_items(self) -> None:
        # 第 2 页去重后只剩旧条目，但重复的那条本身是新的：
        # 用去重结果判断会把整页误判成「都早于窗口」而提前停。
        source = _api_source(page_size=2, max_pages=5)
        pages = [
            _page(_item(1, date="2026-11-10 12:00:00"), _item(9, date="2026-11-11 12:00:00")),
            _page(_item(1, date="2026-11-10 12:00:00"), _item(2, date="2025-01-02 00:00:00")),
            _page(_item(3, date="2026-11-12 12:00:00")),
        ]
        result, calls = self._collect(source, pages, cutoff=CUTOFF)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(len(result["items"]), 4)

    def test_fetch_json_receives_api_anchored_validator(self) -> None:
        # 接口型源没有 list_url，必须以 api.url 为锚点做重定向校验，
        # 否则被篡改的接口 302 能把爬虫导向内网。
        source = _api_source(page_size=5)
        self._collect(source, [_page(_item(1))])

        validator = self.validators[0]
        self.assertTrue(callable(validator))
        validator("https://example.com/api/list")  # 配置目标本身放行
        with mock.patch.object(
            news_crawler.socket,
            "getaddrinfo",
            side_effect=OSError("dns blocked in test"),
        ):
            with self.assertRaises(ValueError):
                validator("http://127.0.0.1/admin")

    def test_url_template_without_item_id_is_skipped(self) -> None:
        source = _api_source(url_field="", url_template="https://example.com/detail/{id}")
        first = _item(1)
        first.pop("originalURL")
        first.pop("id")
        with self.assertLogs("news_crawler", level="WARNING") as logs:
            result, _ = self._collect(source, [_page(first)])

        self.assertEqual(result["items"], [])
        self.assertTrue(any("缺少 id 字段" in line for line in logs.output))


class ApiSettingsTest(unittest.TestCase):
    def test_defaults_and_overrides(self) -> None:
        settings = news_crawler._api_settings({"name": "s", "api": {"items_path": "a.b"}})

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings["method"], "POST")
        self.assertEqual(settings["page_param"], "pageNum")
        self.assertEqual(settings["page_size_param"], "pageSize")
        self.assertEqual(settings["url_field"], "originalURL")
        self.assertEqual(settings["title_field"], "name")
        self.assertEqual(settings["date_field"], "publishTime")
        self.assertEqual(settings["content_field"], "content")
        self.assertTrue(settings["content_is_html"])
        self.assertEqual(settings["start_page"], 1)

    def test_absent_api_returns_none(self) -> None:
        self.assertIsNone(news_crawler._api_settings({"name": "s"}))

    def test_invalid_method_falls_back_to_post(self) -> None:
        settings = news_crawler._api_settings(
            {"name": "s", "api": {"items_path": "a", "method": "PUT"}}
        )

        assert settings is not None
        self.assertEqual(settings["method"], "POST")

    def test_dig_supports_list_index(self) -> None:
        data = {"data": {"list": [{"name": "first"}]}}

        self.assertEqual(news_crawler._dig(data, "data.list.0.name"), "first")
        self.assertIsNone(news_crawler._dig(data, "data.list.5.name"))
        self.assertIsNone(news_crawler._dig(data, "data.missing.name"))

    def test_html_to_text_keeps_paragraph_breaks_and_images(self) -> None:
        text, images = news_crawler._html_to_text(
            '<p>第一段</p><p>第二段<img src="https://example.com/a.png"></p>'
        )

        self.assertIn("第一段\n", text)
        self.assertIn("第二段", text)
        self.assertEqual(images, ["https://example.com/a.png"])

    def test_decode_bytes_falls_back_to_gbk(self) -> None:
        # 政务系统常见 GBK 接口：先说 utf-8 再退 gb18030，
        # 绝不能直接 utf-8+replace（那样整批字段静默变乱码）。
        import url_extract

        raw = '{"name":"上海市中小企业发展专项"}'.encode("gbk")
        self.assertEqual(
            url_extract._decode_bytes(raw, "gbk"), '{"name":"上海市中小企业发展专项"}'
        )
        # 响应头没给 charset 时也要能兜回来
        self.assertEqual(
            url_extract._decode_bytes(raw, None), '{"name":"上海市中小企业发展专项"}'
        )
        # UTF-8 响应不受影响
        utf8 = '{"name":"中文"}'.encode()
        self.assertEqual(url_extract._decode_bytes(utf8, "utf-8"), '{"name":"中文"}')

    def test_decode_bytes_sniffs_charset_from_html_head(self) -> None:
        # fetch_html 没有响应头 charset 时靠 meta 嗅探；抽成共用函数后
        # 这个参数一旦被丢掉/写反，编码就会整体错判。
        import url_extract

        raw = '<meta charset="big5"><p>繁體中文政策</p>'.encode("big5")
        self.assertNotIn("繁體中文政策", url_extract._decode_bytes(raw, None))
        self.assertIn(
            "繁體中文政策", url_extract._decode_bytes(raw, None, sniff_html=True)
        )
        # 响应头已声明时不嗅探覆盖
        self.assertIn(
            "繁體中文政策", url_extract._decode_bytes(raw, "big5", sniff_html=True)
        )

    def test_decode_bytes_logs_when_declared_charset_is_wrong(self) -> None:
        # 声明的 charset 解不开时静默降级会让运维看不出走了哪个编码。
        import url_extract

        raw = '{"name":"上海市中小企业发展专项"}'.encode("gbk")
        with self.assertLogs("url_extract", level="INFO") as logs:
            text = url_extract._decode_bytes(raw, "utf-8")

        self.assertEqual(text, '{"name":"上海市中小企业发展专项"}')
        self.assertTrue(any("gb18030" in line for line in logs.output))

    def test_html_to_text_drops_scripts_and_separates_cells(self) -> None:
        text, _ = news_crawler._html_to_text(
            "<p>正文第一段</p>"
            '<script>var a=1;console.log("x")</script>'
            "<style>.c{color:red}</style>"
            "<table><tr><td>材料A</td><td>材料B</td></tr></table>"
        )

        self.assertNotIn("console.log", text)
        self.assertNotIn("color:red", text)
        # 单元格之间必须有分隔，否则会粘成「材料A材料B」。
        self.assertIn("材料A\n材料B", text)


class ApiTransportTest(unittest.TestCase):
    """fetch_json 的传输层：重定向校验与跨源凭据剥离。"""

    def _redirect(self, req, newurl: str, seen: list[str]):
        import url_extract

        handler = url_extract._ValidatingRedirectHandler(seen.append)
        return handler.redirect_request(req, None, 302, "Found", {}, newurl)

    def test_redirect_strips_credentials_across_origins(self) -> None:
        # api.headers 里常放 Cookie/Authorization：urllib 默认把它们原样
        # 复制给重定向目标（只去掉 Content-Length/Content-Type），一个恶意
        # 302 就能把接口凭据交给第三方。
        import urllib.request

        req = urllib.request.Request(
            "https://api.example.com/list",
            headers={
                "Cookie": "session=1",
                "Authorization": "Bearer token",
                "X-Trace": "keep",
            },
            method="POST",
            data=b"{}",
        )
        seen: list[str] = []
        new_req = self._redirect(req, "https://evil.example.net/steal", seen)

        assert new_req is not None
        self.assertEqual(seen, ["https://evil.example.net/steal"])
        merged = {k.lower() for k in list(new_req.headers) + list(new_req.unredirected_hdrs)}
        self.assertNotIn("cookie", merged)
        self.assertNotIn("authorization", merged)
        self.assertIn("x-trace", merged)

    def test_redirect_keeps_credentials_on_same_origin(self) -> None:
        # 同源跳转（如 /list -> /list/v2）必须保留凭据，否则正常接口直接 401。
        import urllib.request

        req = urllib.request.Request(
            "https://api.example.com/list",
            headers={"Cookie": "session=1", "Authorization": "Bearer token"},
            method="GET",
        )
        new_req = self._redirect(req, "https://api.example.com/v2/list", [])

        assert new_req is not None
        merged = {k.lower() for k in list(new_req.headers) + list(new_req.unredirected_hdrs)}
        self.assertIn("cookie", merged)
        self.assertIn("authorization", merged)

    def test_redirect_rejects_target_before_following(self) -> None:
        import urllib.request

        req = urllib.request.Request("https://api.example.com/list", method="GET")

        def reject(target: str) -> None:
            raise ValueError(f"拒绝: {target}")

        import url_extract

        handler = url_extract._ValidatingRedirectHandler(reject)
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/")

    def test_fetch_json_installs_pinned_handlers_with_validator(self) -> None:
        # 只装 HTTPSHandler 时 build_opener 会补上默认的重定向处理器，
        # 接口就能被 302 引到内网；传 validator 必须换成带钉扎的整套。
        import url_extract

        with mock.patch.object(url_extract.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = OSError("boom")
            with self.assertRaises(OSError):
                url_extract.fetch_json(
                    "https://example.com/api", url_validator=lambda url: None
                )
        handlers = [type(item).__name__ for item in opener.call_args.args]
        self.assertEqual(
            handlers,
            [
                "ProxyHandler",
                "_ValidatingRedirectHandler",
                "_PinnedHTTPSHandler",
                "_PinnedHTTPHandler",
            ],
        )

    def test_fetch_json_without_validator_keeps_default_handlers(self) -> None:
        # 反向守护：直接调用（无 url_validator）不该被强行钉扎/禁代理。
        import url_extract

        with mock.patch.object(url_extract.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = OSError("boom")
            with self.assertRaises(OSError):
                url_extract.fetch_json("https://example.com/api")
        handlers = [type(item).__name__ for item in opener.call_args.args]
        self.assertEqual(handlers, ["HTTPSHandler"])


class ApiProcessEntryTest(unittest.TestCase):
    """接口型条目自带正文时不再请求详情页。"""

    def test_inline_body_skips_detail_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            news_crawler.init_db(db_path).close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                entry = {
                    "title": "政策标题",
                    "url": "https://example.com/p/1.html",
                    "date_text": "2026-11-10 12:00:00",
                    "parsed": datetime(
                        2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")
                    ),
                    "deadline_text": "",
                    "deadline": None,
                    "project_status": "",
                    "content": "正文段落",
                    "raw_content": "正文段落",
                    "images": [],
                }
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    side_effect=AssertionError("接口型条目不应请求详情页"),
                ):
                    status = news_crawler.process_entry(
                        conn, _api_source(), entry, CUTOFF, False
                    )

                self.assertEqual(status, "ok")
                row = conn.execute(
                    "SELECT title, title_source, content, raw_content, status,"
                    " publish_time, publish_time_source, timezone FROM articles"
                ).fetchone()
                self.assertEqual(row["status"], "ok")
                self.assertEqual(row["title"], "政策标题")
                self.assertEqual(row["title_source"], "api_field")
                self.assertEqual(row["content"], "正文段落")
                self.assertEqual(row["raw_content"], "正文段落")
                self.assertEqual(row["publish_time"], "2026-11-10 12:00:00")
                self.assertEqual(row["publish_time_source"], "list")
                self.assertEqual(row["timezone"], "Asia/Shanghai")
            finally:
                conn.close()

    def test_entry_without_body_still_fetches_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            news_crawler.init_db(db_path).close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                entry = {
                    "title": "列表标题",
                    "url": "https://example.com/p/2.html",
                    "date_text": "2026-11-10 12:00:00",
                    "parsed": datetime(
                        2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")
                    ),
                    "deadline_text": "",
                    "deadline": None,
                    "project_status": "",
                }
                detail = {
                    "title": "详情标题",
                    "content": "详情正文。" * 40,
                    "publish_time": "",
                    "publish_time_source": "",
                    "title_source": "gne_default",
                    "author": "",
                    "images": [],
                }
                with (
                    mock.patch.object(
                        news_crawler,
                        "fetch_article_html",
                        return_value=("<html></html>", False),
                    ) as fetched,
                    mock.patch.object(
                        news_crawler, "extract_article", return_value=detail
                    ),
                ):
                    status = news_crawler.process_entry(
                        conn, _api_source(), entry, CUTOFF, False
                    )

                self.assertEqual(status, "ok")
                self.assertEqual(fetched.call_count, 1)
                row = conn.execute("SELECT title, title_source FROM articles").fetchone()
                self.assertEqual(row["title"], "详情标题")
                self.assertEqual(row["title_source"], "gne_default")
            finally:
                conn.close()

    def test_entry_with_only_content_still_skips_detail_fetch(self) -> None:
        # 直接构造 entry 的调用方可能只填 content（没有换行版 raw_content）。
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "news.db")
            news_crawler.init_db(db_path).close()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                entry = {
                    "title": "政策标题",
                    "url": "https://example.com/p/3.html",
                    "date_text": "2026-11-10 12:00:00",
                    "parsed": datetime(
                        2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")
                    ),
                    "deadline_text": "",
                    "deadline": None,
                    "project_status": "",
                    "content": "只有 content 的正文",
                }
                with mock.patch.object(
                    news_crawler,
                    "fetch_article_html",
                    side_effect=AssertionError("有 content 时不应请求详情页"),
                ):
                    status = news_crawler.process_entry(
                        conn, _api_source(), entry, CUTOFF, False
                    )

                self.assertEqual(status, "ok")
                row = conn.execute("SELECT content FROM articles").fetchone()
                self.assertEqual(row["content"], "只有 content 的正文")
            finally:
                conn.close()


class ApiLoadConfigTest(unittest.TestCase):
    def test_load_config_normalizes_api_source(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "sources": [
                            {
                                "name": "shpolicy",
                                "timezone": "",
                                "api": {
                                    "url": "https://example.com/api",
                                    "items_path": "data.list",
                                    "method": "post",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = news_crawler.load_config(str(path))

        source = config["sources"][0]
        self.assertEqual(source["timezone"], "Asia/Shanghai")
        self.assertEqual(source["api"]["method"], "POST")
        self.assertEqual(source["api"]["items_path"], "data.list")
        self.assertEqual(source["listing_type"], "news")

    def test_load_config_warns_on_api_with_project_listing(self) -> None:
        # 这个组合没有实现：接口正文与截止时间都不会被采用，
        # 必须在加载配置时就告警，而不是静默写出缺正文的项目行。
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "sources": [
                            {
                                "name": "api_project",
                                "listing_type": "project_application",
                                "deadline_xpath": "./td[2]",
                                "api": {
                                    "url": "https://example.com/api",
                                    "items_path": "data.list",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertLogs("news_crawler", level="WARNING") as logs:
                news_crawler.load_config(str(path))

        self.assertTrue(
            any("listing_type=project_application" in line for line in logs.output)
        )

    def test_load_config_quiet_on_api_with_news_listing(self) -> None:
        # 反向守护：告警条件必须限定在项目模式，新闻源不能跟着挨告警。
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "sources": [
                            {
                                "name": "api_news",
                                "api": {
                                    "url": "https://example.com/api",
                                    "items_path": "data.list",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertNoLogs("news_crawler", level="WARNING"):
                config = news_crawler.load_config(str(path))

        self.assertEqual(config["sources"][0]["listing_type"], "news")


if __name__ == "__main__":
    unittest.main()
