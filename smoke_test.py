"""Offline smoke test used by deploy scripts.

Checks that the project can be imported, dates parse correctly, GNE can
extract from a bundled sample page, and SQLite can be initialized.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from smoke_fixture import SAMPLE_RELATIVE_PATH, load_sample_html


def main() -> None:
    import playwright  # noqa: F401

    import url_extract  # noqa: F401
    from gne import GeneralNewsExtractor
    from news_crawler import (
        SCHEMA_VERSION,
        init_db,
        load_config,
        parse_datetime,
        save_article,
    )

    root = Path(__file__).resolve().parent

    date_cases = {
        "2026-09-04 10:00:00": "2026-09-04",
        "2026年09月04日 18:44:09": "2026-09-04",
        "04 2026.09": "2026-09-04",
    }
    for text, expected in date_cases.items():
        parsed = parse_datetime(text)
        if parsed is None or parsed.strftime("%Y-%m-%d") != expected:
            raise AssertionError(f"date parse failed: {text!r} -> {parsed}")

    # 视频页适配最小回归用例：防误判 / 标题截断 / 日期优先级
    ordinary_url = "https://example.com/news/2026/10/30/abc.html?related=/video/1"
    ordinary_html = (
        "<html><head><title>普通文章</title></head><body><article>"
        '<video><source type="video/mp4" src="a.mp4"></video><p>正文</p>'
        "</article></body></html>"
    )
    if url_extract.is_video_page(ordinary_url, ordinary_html):
        raise AssertionError("embedded video ordinary article misclassified as video page")


    # 普通文章的 JSON-LD @graph 内嵌 VideoObject 时不得误判为视频页
    article_jsonld_html = (
        '<html><head><meta property="og:type" content="article">'
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"NewsArticle","headline":"x"},{"@type":"VideoObject","name":"v"}]}'
        "</script></head><body><article>正文</article></body></html>"
    )
    if url_extract.is_video_page("https://example.com/news/2026/10/30/abc.html", article_jsonld_html):
        raise AssertionError("article JSON-LD @graph video misclassified as video page")

    # 主实体的 @type 为数组 [VideoObject, NewsArticle] 时仍应识别为视频页
    array_video_html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":["VideoObject","NewsArticle"]}'
        "</script></head><body></body></html>"
    )
    if not url_extract.is_video_page("https://example.com/news/2026/10/30/abc.html", array_video_html):
        raise AssertionError("JSON-LD @type array video page not detected")

    # 非 ISO 日期不得被按字符 T 截断
    rfc_date_html = (
        '<html><head><meta property="og:type" content="video.other">'
        '<meta property="og:title" content="视频标题">'
        '<meta property="article:published_time" content="Tue, 30 Oct 2026 10:00:00 GMT">'
        "</head><body></body></html>"
    )
    rfc_meta = url_extract.extract_video_meta(rfc_date_html, "https://example.com/video/1")
    if rfc_meta is None or rfc_meta["publish_time"] != "Tue, 30 Oct 2026 10:00:00 GMT":
        raise AssertionError(f"RFC date wrongly truncated: {rfc_meta}")

    video_html = (
        '<html><head><meta property="og:type" content="video.other">'
        '<meta property="og:title" content="COVID-19疫苗获批-中新网视频">'
        '<meta name="date" content="2026-10-30">'
        '<script type="application/ld+json">'
        '{"@type":"VideoObject","datePublished":"2020-01-05"}</script>'
        "</head><body></body></html>"
    )
    video_url = "https://example.com/shipin/2026/10-30/news1.shtml"
    if not url_extract.is_video_page(video_url, video_html):
        raise AssertionError("video page not detected")
    video_meta = url_extract.extract_video_meta(video_html, video_url)
    if video_meta is None or video_meta["title"] != "COVID-19疫苗获批":
        raise AssertionError(f"video title truncated: {video_meta}")
    if video_meta["publish_time"] != "2020-01-05":
        raise AssertionError(f"video publish priority wrong: {video_meta['publish_time']}")

    # 图片地址补全：协议相对 //cdn/... 必须变成绝对 URL，否则下游无法直接使用
    if url_extract.normalize_images(
        ["//n.sinaimg.cn/a.jpg", "https://x/b.png", "", "//n.sinaimg.cn/a.jpg"],
        "https://news.sina.com.cn/c/1.html",
    ) != ["https://n.sinaimg.cn/a.jpg", "https://x/b.png"]:
        raise AssertionError("normalize_images failed")

    # 只有“被拒绝”的 HTTP 状态才值得回退浏览器；404/500 渲染后仍是同样错误
    if 404 in url_extract.BROWSER_FALLBACK_HTTP_CODES:
        raise AssertionError("404 must not trigger browser fallback")
    if not {401, 403, 406, 429} <= url_extract.BROWSER_FALLBACK_HTTP_CODES:
        raise AssertionError("anti-bot status codes must trigger browser fallback")

    # gzip 解码：正常压缩必须解开，未知编码/截断流必须显式失败而不是静默透传
    import gzip as gz
    import zlib

    plain = "<html><body>新闻正文</body></html>".encode()
    if url_extract._decompress(gz.compress(plain), "gzip") != plain:
        raise AssertionError("gzip body not decompressed")
    if url_extract._decompress(plain, None) != plain:
        raise AssertionError("uncompressed body must pass through")
    if url_extract._decompress(plain, "identity") != plain:
        raise AssertionError("identity encoding must pass through")
    twice = gz.compress(gz.compress(plain))
    if url_extract._decompress(twice, "gzip, gzip") != plain:
        raise AssertionError("stacked gzip must be decoded in reverse order")
    for encoding, body in (("br", plain), ("gzip", gz.compress(plain)[:-5])):
        try:
            url_extract._decompress(body, encoding)
        except url_extract.UnsupportedContentEncoding:
            pass
        else:
            raise AssertionError(f"{encoding} body must not be silently passed through")
    if url_extract._decompress(zlib.compress(plain), "deflate") != plain:
        raise AssertionError("zlib-wrapped deflate not decoded")
    raw_deflate = zlib.compress(plain)[2:-1]
    if url_extract._decompress(raw_deflate, "deflate") != plain:
        raise AssertionError("raw deflate stream not decoded")

    # 图片只保留 http(s)：data:/javascript:/ftp: 不该出现在对外图片列表里
    if url_extract.normalize_images(
        ["data:image/png;base64,AAA", "javascript:alert(1)", "ftp://x/a.png"],
        "https://news.example.com/a.html",
    ) != []:
        raise AssertionError("non-http(s) image schemes must be dropped")

    # 镜像不带 tests/（62 个上游样本，见 .dockerignore），容器内冒烟用内置样本，
    # 仍然走完整的 GNE 抽取路径，宿主机上则始终比对真实上游页面。
    if not (root / SAMPLE_RELATIVE_PATH).is_file():
        print("（未找到 tests/163/1.html，改用内置样本）")
    sample_html = load_sample_html(root)
    result = GeneralNewsExtractor().extract(sample_html)
    if not result.get("title") or not result.get("content"):
        raise AssertionError("GNE failed to extract sample page")

    config = load_config(str(root / "sources.json"))
    if not isinstance(config.get("sources"), list):
        raise AssertionError("sources.json is not a valid config template")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "smoke.db")
        conn = init_db(db_path)
        try:
            conn.execute("SELECT 1 FROM articles LIMIT 1")
            # 列表接口依赖覆盖索引；缺了会让 10 万行库的分页退化成全表排序
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='articles'"
                )
            }
            required = {
                "idx_articles_status_published",
                "idx_articles_source_cover",
                "idx_articles_status_updated",
            }
            if not required <= indexes:
                raise AssertionError(f"missing performance indexes: {required - indexes}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
            if "content_length" not in columns:
                raise AssertionError(f"missing content_length column: {columns}")
        finally:
            conn.close()

        # old 只在“存储的发布时间仍早于窗口”时跳过；窗口放宽后必须重抓
        conn = init_db(db_path)
        try:
            from news_crawler import save_article, should_skip_stored

            cutoff_wide = parse_datetime("2010-01-01", None, "Asia/Shanghai")
            cutoff_narrow = parse_datetime("2026-11-01", None, "Asia/Shanghai")
            assert cutoff_wide is not None and cutoff_narrow is not None
            save_article(
                conn,
                "smoke",
                "https://example.com/old.html",
                publish_time="2026-10-30 10:00:00",
                status="old",
            )
            if not should_skip_stored(
                conn, "smoke", "https://example.com/old.html", cutoff_narrow, "Asia/Shanghai"
            ):
                raise AssertionError("old within window should be skipped")
            if should_skip_stored(
                conn, "smoke", "https://example.com/old.html", cutoff_wide, "Asia/Shanghai"
            ):
                raise AssertionError("old must be re-crawled after widening the window")
        finally:
            conn.close()

        # v2 → 当前版本的老库升级：删掉 v3 的两条性能索引、退回 v4 增加的 raw_content 列，
        # 再把版本退回 2，init_db 必须把三者都补回来。
        # 这是生产上最容易出问题的一条路径（新库直接建、老库靠迁移补建）。
        legacy = sqlite3.connect(db_path)
        try:
            legacy.execute("DROP INDEX IF EXISTS idx_articles_status_published")
            legacy.execute("DROP INDEX IF EXISTS idx_articles_source_cover")
            # 不能用 ALTER TABLE ... DROP COLUMN 造老库：它要求 SQLite >= 3.35，
            # 而项目只声明 Python >= 3.10。改为重建一张 v2 结构的表并搬数据，
            # 在所有支持的 SQLite 版本上都能跑。
            legacy.executescript(
                """
                ALTER TABLE articles RENAME TO articles_v4;
                CREATE TABLE articles (
                    source        TEXT NOT NULL,
                    url           TEXT NOT NULL,
                    list_title    TEXT,
                    title         TEXT,
                    publish_time  TEXT,
                    author        TEXT,
                    content       TEXT,
                    images        TEXT,
                    status        TEXT NOT NULL,
                    error         TEXT,
                    first_seen_at TEXT,
                    updated_at    TEXT,
                    PRIMARY KEY (source, url)
                );
                INSERT INTO articles (
                    source, url, list_title, title, publish_time, author,
                    content, images, status, error, first_seen_at, updated_at
                )
                SELECT source, url, list_title, title, publish_time, author,
                       content, images, status, error, first_seen_at, updated_at
                FROM articles_v4;
                DROP TABLE articles_v4;
                """
            )
            legacy.execute("PRAGMA user_version = 2")
            legacy.commit()
        finally:
            legacy.close()
        conn = init_db(db_path)
        try:
            upgraded = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='articles'"
                )
            }
            required_upgraded = {
                "idx_articles_status_published",
                "idx_articles_source_cover",
                "idx_articles_status_updated",
            }
            if not required_upgraded <= upgraded:
                raise AssertionError(f"v2 -> v7 upgrade missed indexes: {upgraded}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
            for required_column in (
                "raw_content",
                "content_length",
                "publish_time_source",
                "publish_time_raw",
                "list_publish_time",
                "title_source",
            ):
                if required_column not in columns:
                    raise AssertionError(
                        f"v2 -> v7 upgrade missed {required_column}: {columns}"
                    )
            legacy_row = conn.execute(
                "SELECT publish_time_source, publish_time_raw, "
                "list_publish_time, title_source FROM articles LIMIT 1"
            ).fetchone()
            if legacy_row is None:
                raise AssertionError("v2 -> v7 upgrade lost legacy article row")
            if any(value is not None for value in legacy_row):
                raise AssertionError(
                    f"v2 -> v7 upgrade should leave new columns NULL: {legacy_row}"
                )
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current != SCHEMA_VERSION:
                raise AssertionError(
                    f"v2 -> v{SCHEMA_VERSION} upgrade did not bump user_version: {current}"
                )
        finally:
            conn.close()

        # ok 路径曾漏传 raw_content，导致换行正文只存在于 unknown_date 分支。
        # 这里同时验证写入与「本次没传就不覆盖」的部分更新语义。
        conn = init_db(db_path)
        try:
            smoke_url = "https://example.com/smoke/raw.html"
            save_article(
                conn,
                "smoke",
                smoke_url,
                title="换行正文",
                publish_time="2026-10-01 09:30:00",
                timezone="Asia/Shanghai",
                content="第一段 第二段",
                raw_content="第一段\n第二段",
                images=["https://example.com/a.jpg"],
                status="ok",
            )
            stored = conn.execute(
                "SELECT content, raw_content, images FROM articles WHERE url = ?",
                (smoke_url,),
            ).fetchone()
            if stored is None or stored[1] != "第一段\n第二段":
                raise AssertionError(f"raw_content not stored: {stored}")
            if stored[0] != "第一段 第二段":
                raise AssertionError(f"content should stay single-line: {stored[0]!r}")
            if json.loads(stored[2]) != ["https://example.com/a.jpg"]:
                raise AssertionError(f"images not stored: {stored[2]!r}")

            # images=[] 表示“明确无图”，必须能清空旧图片；省略 images 才保留。
            save_article(conn, "smoke", smoke_url, images=[], status="ok")
            cleared = conn.execute(
                "SELECT images FROM articles WHERE url = ?", (smoke_url,)
            ).fetchone()
            if cleared is None or json.loads(cleared[0]) != []:
                raise AssertionError(f"images not cleared by []: {cleared}")

            save_article(
                conn,
                "smoke",
                smoke_url,
                status="error",
                error="模拟抓取失败",
            )
            kept = conn.execute(
                "SELECT raw_content, images FROM articles WHERE url = ?",
                (smoke_url,),
            ).fetchone()
            if kept is None or kept[0] != "第一段\n第二段":
                raise AssertionError(f"raw_content lost on partial update: {kept}")
            if json.loads(kept[1]) != []:
                raise AssertionError(f"images changed when omitted: {kept[1]!r}")
        finally:
            conn.close()

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
