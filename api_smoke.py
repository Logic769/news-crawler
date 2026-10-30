"""Offline API smoke test: FastAPI + SQLite + a local article fixture."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

import news_crawler
from news_api.app import create_app
from news_api.jobs import JobManager
from news_api.netguard import UnsafeUrlError, ensure_url_allowed
from news_api.settings import Settings

FIXTURE = Path(__file__).resolve().parent / "tests" / "163" / "1.html"
API_KEY = "smoke-secret"


class _ArticleHandler(BaseHTTPRequestHandler):
    body = b""
    flaky_fail = False

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/flaky") and _ArticleHandler.flaky_fail:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args: object) -> None:
        return


def _request(base: str, method: str, path: str, payload: dict | None = None, key: str | None = API_KEY) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, str]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        if not thread.is_alive():
            raise AssertionError("API 线程提前退出")
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}"


def _wait_health(base: str, deadline: float) -> None:
    while time.time() < deadline:
        try:
            status, body = _request(base, "GET", "/v1/health", key=None)
            if status == 200 and body.get("database") == "ok":
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError("API 未在限定时间内就绪")


def _wait_job(base: str, job_id: str, deadline: float) -> dict:
    job: dict = {}
    while time.time() < deadline:
        status, job = _request(base, "GET", f"/v1/jobs/{job_id}")
        assert status == 200, (status, job)
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"作业 {job_id} 未在限定时间内结束：{job}")


def main() -> None:
    assert FIXTURE.exists(), f"缺少测试样本: {FIXTURE}"
    try:
        ensure_url_allowed("http://127.0.0.1/")
    except UnsafeUrlError:
        pass
    else:
        raise AssertionError("私网 URL 未被拒绝")
    ensure_url_allowed("http://127.0.0.1/", allow_private=True)
    _ArticleHandler.body = FIXTURE.read_bytes()
    fixture_server = ThreadingHTTPServer(("127.0.0.1", 0), _ArticleHandler)
    fixture_port = fixture_server.server_address[1]
    threading.Thread(target=fixture_server.serve_forever, daemon=True).start()

    with TemporaryDirectory(prefix="news-api-smoke-") as tmp:
        db_path = str(Path(tmp) / "news.db")
        config_path = str(Path(tmp) / "sources.local.json")
        Path(config_path).write_text(
            json.dumps(
                {
                    "workers": 1,
                    "days": 3,
                    "timezone": "Asia/Shanghai",
                    "sources": [
                        {
                            "name": "fixture",
                            "list_url": "https://example.com/news",
                            "item_xpath": "//li",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        conn = news_crawler.init_db(db_path)
        article_id = news_crawler.save_article(
            conn,
            "fixture",
            "https://example.com/articles/1",
            title="接口冒烟测试标题",
            publish_time="2026-10-30 10:00:00",
            author="测试作者",
            content="这是接口冒烟测试正文。" * 20,
            images=["https://example.com/1.jpg"],
            status="ok",
        )
        conn.close()

        settings = Settings(
            db_path=db_path,
            config_path=config_path,
            api_key=API_KEY,
            job_workers=1,
            timezone="Asia/Shanghai",
            allow_private_urls=True,
        )
        app = create_app(settings)
        server, thread, base = _serve(app)

        try:
            _wait_health(base, time.time() + 20)

            status, body = _request(base, "GET", "/v1/articles", key=None)
            assert status == 401, (status, body)
            status, body = _request(base, "GET", "/v1/health", key=None)
            assert status == 200 and body["articles"] == 1, (status, body)
            assert body["time"].endswith("+08:00"), body

            status, body = _request(base, "GET", "/v1/articles?page_size=10")
            assert status == 200, (status, body)
            assert body["meta"]["total"] == 1, body
            item = body["data"][0]
            assert item["id"] == article_id, item
            assert item["content"] is None, item
            assert item["published_at"].endswith("+08:00"), item
            assert item["published_at_utc"].endswith("Z"), item
            assert item["author"] == "测试作者", item

            status, no_match = _request(
                base, "GET", "/v1/articles?q=" + urllib.parse.quote("100%")
            )
            assert status == 200 and no_match["meta"]["total"] == 0, no_match

            status, detail = _request(base, "GET", f"/v1/articles/{article_id}")
            assert status == 200 and detail["content"], detail

            status, with_content = _request(
                base, "GET", "/v1/articles?page_size=10&include_content=true"
            )
            assert status == 200, (status, with_content)
            assert with_content["meta"]["total"] == 1, with_content
            full_item = with_content["data"][0]
            assert full_item["content"], full_item
            assert full_item["content_length"] == item["content_length"], full_item

            status, sources = _request(base, "GET", "/v1/sources")
            assert status == 200 and sources["data"][0]["source"] == "fixture", sources

            status, stats_body = _request(base, "GET", "/v1/stats")
            assert status == 200 and stats_body["total"] == 1, stats_body
            assert stats_body["generated_at"].endswith("+08:00"), stats_body

            status, underscore = _request(
                base, "GET", "/v1/articles?q=" + urllib.parse.quote("_")
            )
            assert status == 200 and underscore["meta"]["total"] == 0, underscore

            import news_api.app as app_module

            original_count = app_module.count_articles
            app_module.count_articles = lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("db down")
            )
            try:
                status, degraded = _request(base, "GET", "/v1/health", key=None)
            finally:
                app_module.count_articles = original_count
            assert status == 503, (status, degraded)
            assert degraded["error"]["code"] == "database_unavailable", degraded

            strict_settings = Settings(
                db_path=db_path,
                config_path=config_path,
                api_key=API_KEY,
                job_workers=1,
                timezone="Asia/Shanghai",
                allow_private_urls=False,
            )
            strict_server, strict_thread, strict_base = _serve(create_app(strict_settings))
            try:
                _wait_health(strict_base, time.time() + 20)
                status, blocked = _request(
                    strict_base,
                    "POST",
                    "/v1/jobs",
                    {"type": "extract", "url": "http://127.0.0.1/"},
                )
                assert status == 400, (status, blocked)
            finally:
                strict_server.should_exit = True
                strict_thread.join(timeout=10)

            auth_settings = Settings(
                db_path=db_path,
                config_path=config_path,
                api_key=None,
                job_workers=1,
                timezone="Asia/Shanghai",
                allow_private_urls=True,
                require_auth=True,
            )
            auth_server, auth_thread, auth_base = _serve(create_app(auth_settings))
            try:
                _wait_health(auth_base, time.time() + 20)
                status, denied = _request(auth_base, "GET", "/v1/articles", key=None)
                assert status == 401, (status, denied)
                status, denied_job = _request(
                    auth_base, "POST", "/v1/jobs", {"type": "crawl"}, key=None
                )
                assert status == 401, (status, denied_job)
            finally:
                auth_server.should_exit = True
                auth_thread.join(timeout=10)

            fixture_url = f"http://127.0.0.1:{fixture_port}/article.html"
            status, created = _request(
                base, "POST", "/v1/jobs", {"type": "extract", "url": fixture_url}
            )
            assert status == 202, (status, created)
            job = _wait_job(base, created["data"]["id"], time.time() + 60)
            assert job["status"] == "succeeded", job
            assert job["result"]["status"] == "ok", job
            assert job["result"]["article_id"], job

            regression_conn = news_crawler.init_db(db_path)
            try:
                news_crawler.save_article(
                    regression_conn,
                    "on_demand",
                    fixture_url,
                    status="error",
                    error="模拟抓取失败",
                )
            finally:
                regression_conn.close()
            status, preserved = _request(
                base, "GET", f"/v1/articles/{job['result']['article_id']}"
            )
            assert status == 200 and preserved["status"] == "error", preserved
            assert preserved["content"], preserved
            assert preserved["title"], preserved
            status, extracted = _request(
                base, "GET", f"/v1/articles/{job['result']['article_id']}"
            )
            assert status == 200 and extracted["source"] == "on_demand", extracted

            status, jobs = _request(base, "GET", "/v1/jobs?page_size=10")
            assert status == 200 and jobs["meta"]["total"] == 1, jobs

            # 真实失败路径：同一 URL 先成功入库，再让源站返回 500，
            # 验证失败只标记 status/error，已抓到的正文不会被清空。
            flaky_url = f"http://127.0.0.1:{fixture_port}/flaky.html"
            status, created_flaky = _request(
                base, "POST", "/v1/jobs", {"type": "extract", "url": flaky_url}
            )
            assert status == 202, (status, created_flaky)
            flaky_job = _wait_job(base, created_flaky["data"]["id"], time.time() + 60)
            assert flaky_job["status"] == "succeeded", flaky_job
            flaky_article_id = flaky_job["result"]["article_id"]
            _ArticleHandler.flaky_fail = True
            try:
                status, failed_created = _request(
                    base, "POST", "/v1/jobs", {"type": "extract", "url": flaky_url}
                )
                assert status == 202, (status, failed_created)
                failed_job = _wait_job(
                    base, failed_created["data"]["id"], time.time() + 60
                )
            finally:
                _ArticleHandler.flaky_fail = False
            assert failed_job["status"] == "failed", failed_job
            status, preserved = _request(
                base, "GET", f"/v1/articles/{flaky_article_id}"
            )
            assert status == 200 and preserved["content"], preserved
            assert preserved["status"] == "error", preserved
            assert preserved["title"], preserved

            raw = sqlite3.connect(db_path)
            try:
                raw.executemany(
                    "INSERT INTO crawl_jobs (id, type, status, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            f"pending-{index}",
                            "crawl",
                            "queued",
                            '{"days": 7}',
                            news_crawler.now_iso(),
                        )
                        for index in range(settings.max_pending_jobs)
                    ],
                )
                raw.commit()
            finally:
                raw.close()
            status, full = _request(
                base, "POST", "/v1/jobs", {"type": "crawl", "days": 9}
            )
            assert status == 429, (status, full)

            same_payload = {"url": "https://example.com/dedupe"}
            raw = sqlite3.connect(db_path)
            try:
                raw.execute(
                    "INSERT INTO crawl_jobs (id, type, status, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "dedupe-1",
                        "extract",
                        "queued",
                        json.dumps(same_payload, ensure_ascii=False, sort_keys=True),
                        news_crawler.now_iso(),
                    ),
                )
                raw.commit()
            finally:
                raw.close()
            manager = JobManager(settings)
            try:
                job = manager.create("extract", same_payload)
            finally:
                manager.shutdown()
            assert job["id"] == "dedupe-1", job

            status, error = _request(
                base, "POST", "/v1/jobs", {"type": "extract"}, key="wrong"
            )
            assert status == 401, (status, error)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            fixture_server.shutdown()
            fixture_server.server_close()

    print("API SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
