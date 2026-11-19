"""EO_Bot 反爬挑战的最小回归测试。"""

from __future__ import annotations

import http.server
import socketserver
import threading
import unittest
from http import HTTPStatus

from url_extract import _solve_eo_bot_cookie, fetch_html

CHALLENGE = (
    '<script>function a(a){function n(){for(var a={wQzOV:_0x649a("0x4"),'
    'iTyzs:function(a,n){return a+n}},n=a[_0x649a("0x5")][_0x649a("0x6")]("|"),e=0;;)'
    '{switch(n[e++]){case"0":t+="EO_Bot_Ssid=";continue;case"1":return t;'
    'case"2":t+="";continue;case"3":t=a[_0x649a("0x7")](t,3100573696);continue;'
    'case"4":var t="";continue}break}}var e={WTKkN:1949044952,bOYDu:525251749,'
    'dtzqS:function(a,n){return a+n},wyeCN:289066673,pCQRM:function(a){return a()}},'
    't=0;return t+=e[_0x649a("0x0")],t+=e[_0x649a("0x1")],'
    't=e[_0x649a("0x2")](t,e[_0x649a("0x3")]),[t,e[_0x649a("0x8")](n)][a]}'
    'var _0x49a6=["wyeCN","4|2|0|3|1","wQzOV","split","iTyzs","pCQRM","cookie",'
    '"location.href=location.href.replace(/[?|&]tads/, \'\')",'
    '"WTKkN","bOYDu","dtzqS"];(function(a,n){var e=function(n){for(;--n;)'
    'a.push(a.shift())};e(++n)})(_0x49a6,0x147);var _0x649a=function(a,n){'
    'a-=0;var e=_0x49a6[a];return e};document[_0x649a("0x9")]='
    '"__tst_status="+a(0)+"#;",document[_0x649a("0x9")]=a(1)+";",'
    'setTimeout(_0x649a("0xa"),0x4b0);</script>'
)


class EoBotCookieTests(unittest.TestCase):
    def test_solves_cookie_from_challenge(self):
        self.assertEqual(
            _solve_eo_bot_cookie(CHALLENGE),
            "__tst_status=2763363374#; EO_Bot_Ssid=3100573696",
        )

    def test_returns_none_for_normal_page(self):
        self.assertIsNone(_solve_eo_bot_cookie("<html><body>normal</body></html>"))

    def test_returns_none_when_session_constant_missing(self):
        broken = CHALLENGE.replace("t=a[_0x649a(\"0x7\")](t,3100573696)", "t=0")
        self.assertIsNone(_solve_eo_bot_cookie(broken))

    def test_fetch_html_retries_with_solved_cookie(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if "__tst_status=2763363374#" in (self.headers.get("Cookie") or ""):
                    body = b"<html><body>REAL PAGE</body></html>"
                    self.send_response(HTTPStatus.OK)
                else:
                    body = CHALLENGE.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                text = fetch_html(url, url_validator=lambda _url: None)
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertIn("REAL PAGE", text)
        self.assertNotIn("EO_Bot_Ssid", text)


if __name__ == "__main__":
    unittest.main()
