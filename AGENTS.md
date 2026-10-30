# 多 Agent 协作开发说明（Project Guide）

> 本文档面向参与本项目的所有开发者与自动化 Agent。
> 开始修改代码前，请先完整阅读本文档；如果本文档与代码实现冲突，以代码为准，并同步更新本文档。

---

## 1. 项目概况

**news-crawler** 是一个基于 GNE（GeneralNewsExtractor）的多站点新闻抓取工具。

它解决的核心问题是：

1. 给一个新闻文章网址，提取标题、发布时间、作者、正文和图片；
2. 配置多个站点的新闻列表页，自动抓取“最近 N 天”的新闻；
3. 对 JavaScript 动态渲染的页面自动回退到 Chrome/Chromium；
4. 结果写入 SQLite，按 `(source, url)` 去重，支持重复运行；
5. 通过 FastAPI 暴露 `/v1` 标准接口，作为下游项目的新闻数据源。

GNE 本身只负责“从 HTML 中抽取新闻信息”，不负责发起 HTTP 请求。本项目的抓取、过滤、渲染、入库逻辑都在 `news_crawler.py` 与 `url_extract.py` 中实现。

### 关键信息

| 项目 | 内容 |
| --- | --- |
| 当前正式版本 | `v1.0.2` |
| 开源协议 | MIT（继承自上游 GeneralNewsExtractor） |
| 主仓库 | https://github.com/Logic769/news-crawler |
| 上游仓库 | https://github.com/kingname/GeneralNewsExtractor |
| 默认分支 | `master` |
| Python 版本要求 | `>=3.10`（pyproject 声明，classifier 覆盖 3.10–3.14） |

---

## 2. 技术栈

| 类别 | 技术 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.10+ | 使用联合类型、泛型语法等 Python 3.10 特性 |
| HTML 解析 | lxml | GNE 抽取核心依赖，爬虫的 XPath 解析也使用它 |
| 配置格式 | JSON | `sources.json` / `sources.local.json` |
| 数据存储 | SQLite（标准库 sqlite3） | 单文件数据库，默认 `/app/data/news.db`（宿主机挂载为 `./data/news.db`） |
| 浏览器回退 | Playwright + Chrome/Chromium | 可选依赖 `[browser]`，处理 JS 渲染页面 |
| HTTP 请求 | urllib（标准库） | 带 User-Agent、编码探测、SSL 降级 |
| 并发模型 | concurrent.futures.ThreadPoolExecutor | 站点级并发，每个站点内部串行 |
| 时区处理 | zoneinfo | 默认 `Asia/Shanghai` |
| 打包 | hatchling + pyproject.toml | 支持 `pip install -e ".[browser,api]"` |
| 依赖锁定 | uv.lock | 提供给使用 uv 的开发者 |
| HTTP API | FastAPI + Uvicorn | 可选依赖 `[api]`，提供 `/v1` 标准新闻接口与异步作业 |
| CI | GitHub Actions | `deploy-test.yml` 执行多 Python 版本静态检查、Docker 构建、容器冒烟与实时新闻抓取验证 |

运行依赖（必装）：

```toml
"lxml>=4.9.1"
"pyyaml>=5.4.1"
```

可选依赖（JS 渲染回退）：

```toml
browser = ["playwright>=1.40"]
```

---

## 3. 目录结构与职责

```text
news-crawler/
├── news_crawler.py                 # 多站点抓取主程序（核心）
├── url_extract.py                  # 单 URL 抓取 + HTTP/浏览器抓取基础设施
├── smoke_test.py                   # 部署后的离线冒烟测试
├── api.py                          # FastAPI 入口（uvicorn api:app）
├── news_api/                       # API 应用：设置、模型、查询、作业队列
├── api_smoke.py                    # API 冒烟测试（本地 HTTP + 临时数据库）
├── verify_live_news.py             # 实时新闻抓取验证（5 静态 / 3 JS / 2 视频）
├── sources.json                    # 仓库自带空模板（会被 Git 跟踪）
├── sources.local.json              # 本地私有站点配置（.gitignore 忽略）
├── Dockerfile                      # 容器镜像定义（含 Playwright Chromium）
├── docker-compose.yml              # Docker Compose 编排（挂载配置/数据）
├── docker/entrypoint.sh            # 容器统一入口（run/init/test/smoke/live-news/api/shell）
├── docker-deploy.sh / docker-deploy.bat  # Docker 一键部署（Linux·macOS / Windows）
├── pyproject.toml                  # 项目元数据与依赖
├── uv.lock                         # uv 依赖锁
├── README.md                       # 面向用户的中文使用文档
├── AGENTS.md                       # 本文档，面向开发者与 Agent
├── gne/                            # 上游 GNE 抽取库
│   ├── __init__.py                 # GeneralNewsExtractor / ListPageExtractor
│   ├── defaults.py                 # 时间/作者/标题等正则与默认 XPath
│   ├── utils.py                    # HTML 预处理、噪音节点移除等
│   ├── classfier/ArticleClassifier.py
│   └── extractor/
│       ├── ContentExtractor.py     # 正文密度算法
│       ├── TitleExtractor.py       # 标题提取
│       ├── TimeExtractor.py        # 发布时间提取
│       ├── AuthorExtractor.py      # 作者/来源提取
│       ├── MetaExtractor.py        # meta 信息提取
│       └── ListExtractor.py        # 实验性列表页提取
├── tests/                          # 上游新闻页面 HTML 样本（约 62 个文件）
├── test_list_page/                 # 上游列表页样本
├── visiable_test/                  # 上游可见性计算样本
├── docs/                           # 上游 Sphinx 文档与图片
├── screenshots/                    # 上游截图
├── site/                           # 上游演示站静态页
├── example.py                      # 遍历 tests/ 抽取正文示例
├── example_list.py                 # 列表页抽取示例
├── example_visiable.py             # 可见性抽取示例
├── nsfc_latest5.py                 # 早期演示脚本（非核心，可后续整理）
└── .github/workflows/deploy-test.yml
```

### 本地私有文件（绝对不要提交）

- `sources.local.json`：真实站点配置；
- `data/news.db`：抓取结果数据库（挂载在 `data/` 目录）；
- 任何包含 Token、Cookie、账号、密码、代理地址的文件。

这些路径已写入 `.gitignore`。

---

## 4. 核心数据流

```text
sources.local.json
        │
        ▼
collect_list_entries()          # 抓取列表页（必要时 Chrome 渲染）
        │
        ▼
parse_list_items()              # XPath 解析标题/链接/日期
        │
        ▼
run_source()                    # 站点级调度、时间过滤、并发
        │
        ▼
process_entry()                 # 单篇文章处理
        │
        ├── fetch_article_html()        # 直接抓取，失败回退浏览器
        ├── extract_article()           # GNE 抽取，正文为占位时回退浏览器
        └── save_article()              # 写入 SQLite（upsert）
```

### 模块职责速查

| 函数 | 位置 | 职责 |
| --- | --- | --- |
| `fetch_html` | `url_extract.py` | 标准库 HTTP 抓取、编码探测、SSL 兼容 |
| `fetch_html_with_browser` | `url_extract.py` | Playwright 无头 Chrome 渲染，返回渲染后 HTML |
| `content_needs_render` | `url_extract.py` | 判断正文是否为空或只是“正在加载”占位 |
| `extract_article` | `url_extract.py` | 单 URL 完整流程：抓取 → GNE → 必要时渲染 |
| `parse_datetime` | `news_crawler.py` | 解析中文/数字/相对日期，返回带时区 datetime |
| `parse_list_items` | `news_crawler.py` | 用站点配置的 XPath 解析列表页 |
| `process_entry` | `news_crawler.py` | 单篇文章的抓取、过滤、抽取、入库 |
| `run_source` | `news_crawler.py` | 单个站点的完整流程与错误隔离 |
| `save_article` | `news_crawler.py` | SQLite upsert，返回稳定 `article_id` |
| `create_app` | `news_api/app.py` | 构建 FastAPI 应用、`/v1` 路由与鉴权 |
| `JobManager` | `news_api/jobs.py` | 异步 crawl/extract 作业队列与持久化 |
| `query_articles` | `news_api/db.py` | 只读分页查询、时间与时区标准化 |
| `resolve_config_path` | `news_crawler.py` | 默认读 `sources.json`，存在 `sources.local.json` 时优先 |

---

## 5. 对外 API

### 5.1 GNE 正文抽取

```python
from gne import GeneralNewsExtractor

extractor = GeneralNewsExtractor()
result = extractor.extract(html)

# result 结构
{
    "title": str,
    "author": str,
    "publish_time": str,
    "content": str,
    "images": list[str],
    "meta": dict,
}
```

可用参数：`title_xpath`、`author_xpath`、`publish_time_xpath`、`host`、`body_xpath`、`normalize`、`noise_node_list`、`with_body_html`、`use_visiable_info`。

正文为空时抛出 `gne.NoContentException`。

### 5.2 单 URL 抓取

```python
from gne import GeneralNewsExtractor
from url_extract import extract_article

result = extract_article("https://example.com/article.html", GeneralNewsExtractor())
```

该函数内部会自动处理：

1. 直接 HTTP 抓取；
2. HTTP 401/403/406/429 时回退浏览器；
3. 网络错误时输出友好提示并返回 `None`；
4. GNE 正文为空或是加载占位时回退浏览器。

### 5.3 HTTP API（/v1）

启动：`docker compose up -d api`，默认 `http://localhost:8000`，交互文档 `/docs`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/articles` | 分页查询文章，默认 `status=ok` 且不含正文 |
| GET | `/v1/articles/{article_id}` | 文章详情，始终含正文 |
| GET | `/v1/sources` | 来源统计 |
| GET | `/v1/stats` | 全局统计 |
| POST | `/v1/jobs` | 提交 `crawl` 或 `extract` 异步作业，返回 202 |
| GET | `/v1/jobs`、`/v1/jobs/{id}` | 作业列表与状态 |
| GET | `/v1/health` | 健康检查（无需鉴权） |

列表参数：`status`、`source`、`published_from`、`published_to`、`q`、`page`、`page_size`、`order`、`include_content`。文章列表与作业列表返回 `{"data": [...], "meta": {...}}`；文章详情、统计、健康检查返回对应对象；`/v1/sources` 返回 `{"data": [...]}`。

设置 `NEWS_API_KEY` 后，除 `/v1/health` 与文档外都需 `X-API-Key` 或 `Authorization: Bearer <key>`。作业状态为 `queued`/`running`/`succeeded`/`failed`，单进程执行，避免 SQLite 并发写冲突。排队 + 运行中的作业上限为 `NEWS_MAX_PENDING_JOBS`（默认 50），超出返回 `429`；相同类型与参数的未完成作业复用同一 ID。`extract` 默认拒绝回环、内网与保留地址（`NEWS_ALLOW_PRIVATE_URLS=1` 可放开）；`fetch_html` 通过自定义 `HTTPRedirectHandler` 对每个重定向目标复用同一校验，默认还以 `allow_browser=False` 禁用浏览器回退（`NEWS_EXTRACT_ALLOW_BROWSER=1` 可放开），避免页面 JS 绕过地址限制。上述限制仍不能替代 API Key。已完成作业保留 30 天后自动清理。未设置 `NEWS_API_KEY` 时默认开放（仅提示 warning）；设置 `NEWS_REQUIRE_AUTH=1` 可在无 key 时直接拒绝未鉴权请求（401）。错误响应统一为 `{"status": <HTTP 码>, "error": {"code", "message", "details"?}}`，参数校验错误为 422 且带 `details`。

---

## 6. 站点配置说明

真实站点配置放 `sources.local.json`，不要提交到 Git。

```json
{
  "workers": 3,
  "days": 3,
  "timezone": "Asia/Shanghai",
  "delay": 0.5,
  "sources": [
    {
      "name": "example",
      "list_url": "https://example.com/news.html",
      "item_xpath": "//ul[contains(@class,'news-list')]/li",
      "title_xpath": ".//a",
      "url_xpath": ".//a/@href",
      "date_xpath": ".//*[contains(@class,'time')]",
      "date_format": "%Y-%m-%d",
      "timezone": "Asia/Shanghai",
      "render": false
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 站点唯一标识，同时作为数据库 `source` 字段 |
| `list_url` | 二选一 | 列表页地址 |
| `list_urls` | 二选一 | 多个列表页地址（分页场景） |
| `item_xpath` | 是 | 每条新闻节点的 XPath |
| `title_xpath` | 否 | 默认第一个 `a` 的文本 |
| `url_xpath` | 否 | 默认第一个 `a` 的 `href` |
| `date_xpath` | 否 | 默认从整条节点文本中自动识别日期 |
| `date_format` | 否 | 指定 `strptime` 格式 |
| `timezone` | 否 | 默认 `Asia/Shanghai` |
| `render` | 否 | 列表页是否强制 Chrome 渲染 |
| `delay` | 否 | 请求间隔秒数，默认 0.5 |

### XPath 编写约定

- `item_xpath` 使用绝对路径（以 `//` 开头）；
- `title_xpath`、`url_xpath`、`date_xpath` 是相对节点路径，建议以 `.//` 开头；
- 如果误写 `//`，代码会自动转换为 `.//`；
- 日期优先从 `date_xpath` 取；取不到时会尝试从文章详情页的 `publish_time` 或 URL 中的日期路径推断；
- 日期解析当前支持：绝对日期、中文日期、月日、相对时间（“3小时前”“昨天”）、`04 2026.09` 这类“日 年.月”格式。

### 添加新站点的标准流程

1. 在 `sources.local.json` 中新增配置；
2. 运行 `test` 命令验证列表解析；
3. 抽查一篇文章验证正文抽取；
4. 用 `--limit-per-source 1` 小批量试跑；
5. 确认无误后再全量运行。

```bash
docker compose run --rm crawler test --config sources.local.json --name example
docker compose run --rm crawler run --config sources.local.json --limit-per-source 1
```

---

## 7. 数据库设计

### 7.1 表结构

```sql
CREATE TABLE IF NOT EXISTS articles (
    source        TEXT NOT NULL,   -- 站点标识
    article_id    TEXT,            -- sha256(source\x1furl) 前 32 位，稳定对外 ID
    url           TEXT NOT NULL,   -- 文章 URL
    list_title    TEXT,            -- 列表页标题
    title         TEXT,            -- GNE 抽取标题
    publish_time  TEXT,            -- 统一为 YYYY-MM-DD HH:MM:SS
    timezone      TEXT,            -- 写入时的站点时区，历史行可能为空
    author        TEXT,
    content       TEXT,            -- 正文纯文本
    images        TEXT,            -- JSON 数组字符串
    status        TEXT NOT NULL,   -- ok/old/no_content/unknown_date/error
    error         TEXT,
    first_seen_at TEXT,
    updated_at    TEXT,
    PRIMARY KEY (source, url)
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id         TEXT PRIMARY KEY,   -- uuid4 hex
    type       TEXT NOT NULL,      -- crawl/extract
    status     TEXT NOT NULL,      -- queued/running/succeeded/failed
    payload    TEXT NOT NULL,      -- 请求参数 JSON
    result     TEXT,               -- 结果 JSON
    error      TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
```

索引：`source`、`status`、`publish_time`、`article_id`（唯一）；`crawl_jobs` 的 `status`、`created_at`、`(type, payload, status)` 去重索引。

旧库升级由 `init_db()` 按 `PRAGMA user_version` 一次性迁移（当前 `SCHEMA_VERSION = 2`，在 `BEGIN IMMEDIATE` 事务内）：`ALTER TABLE` 补 `article_id` 与 `timezone`，按 2000 行分批回填 `article_id` 并建唯一索引与去重索引，完成后置 `user_version`，后续启动直接跳过。首次切换到 `journal_mode=WAL` 前先读当前模式，已是 WAL 则跳过，并对并发首启做短重试（该 PRAGMA 不受 `busy_timeout` 保护）。`crawl_jobs` 中已完成的作业由 `JobManager.start()` 清理 30 天前的记录。

### 7.2 status 语义

| status | 含义 | 是否会被后续运行跳过 |
| --- | --- | --- |
| `ok` | 抓取成功，且在时间范围内 | 是 |
| `old` | 已确认超出时间范围 | 是 |
| `no_content` | 正文抽取失败 | 否，会重试 |
| `unknown_date` | 无法解析日期 | 否，会重试 |
| `error` | 抓取或处理异常 | 否，会重试 |

### 7.3 注意事项

- `publish_time` 在 `process_entry()` 中统一格式化为 `YYYY-MM-DD HH:MM:SS`；
- 用旧版本抓取过的数据可能仍是中文日期格式，需要 `--force` 重抓或重建数据库；
- `content` 入库前会被 `clean_text()` 压缩空白，段落换行会丢失；
- 同一 `(source, url)` 使用 upsert，不会产生重复记录；
- `save_article()` 是**部分更新**：空字符串/空图片表示“这次没拿到”，不会覆盖库中已有值，因此抓取失败或重新分类时只更新 `status`/`error`，已成功入库的正文不会被清空；
- `timezone` 随文章落库，读取时优先使用该列，旧行（NULL）才回落到当前配置推断；`published_from`/`published_to` 仍按站点本地墙钟字符串比较，混合时区时会有偏差；
- `article_id` 由 `sha256(source + \x1f + url)` 派生，不随数据库重建变化，可安全作为下游引用；
- SQLite 启用 WAL 与 `busy_timeout=5000`，API 作业默认单 worker 写入；只读查询使用 `file:...?mode=ro` URI（路径会转义），WAL 库仍要求目录可写（需要 -shm/-wal）。

---

## 8. CLI 命令速查

所有命令都通过 Docker 容器执行（在宿主机项目根目录运行，无需本地安装 Python）：

```bash
docker compose run --rm crawler <子命令> [args]
```

需要直接运行脚本时用 `--entrypoint python` 覆盖入口。

### 初始化数据库

```bash
docker compose run --rm crawler init --db /app/data/news.db
```

### 测试列表解析（不抓正文）

```bash
docker compose run --rm crawler test --config sources.local.json --name example
```

参数：`--limit` 控制每个站点输出条数。

### 运行抓取

```bash
docker compose run --rm crawler run --config sources.local.json --days 3
```

| 参数 | 说明 |
| --- | --- |
| `--config` | 配置文件路径，默认 `sources.json`（存在 `sources.local.json` 时自动切换） |
| `--db` | 数据库路径，容器内默认 `/app/data/news.db` |
| `--days` | 抓取最近 N 天，默认取配置文件中的 `days` |
| `--since` | 起始日期 `YYYY-MM-DD` |
| `--workers` | 站点级并发数 |
| `--limit-per-source` | 每个站点最多处理条数，调试用 |
| `--force` | 忽略已抓记录，强制重抓 |

### 单 URL 抓取

```bash
docker compose run --rm --entrypoint python crawler -X utf8 url_extract.py "https://example.com/article.html"
```

### 启动 HTTP API

```bash
docker compose up -d api
curl http://localhost:8000/v1/health
```

---

## 9. 部署机制

### 9.1 Docker（唯一部署方式）

- 文件：`Dockerfile`、`.dockerignore`、`docker-compose.yml`、`docker/entrypoint.sh`、`docker-deploy.sh`、`docker-deploy.bat`
- 基础镜像：`python:3.12-slim`；**默认镜像自带 Playwright Chromium**（`playwright install --with-deps chromium`，构建期会实际启动一次 Chromium 自检），可用构建参数 `INSTALL_BROWSER=0` 关闭以减小镜像但失去浏览器能力
- 浏览器运行：容器以 root 运行时 Chromium 必须带 `--no-sandbox --disable-dev-shm-usage`（`url_extract.py` 已内置）
- 视频页适配：`/shipin/`、`/video/` 等视频页由 `url_extract.is_video_page()` 识别，改用 meta/JSON-LD/URL 提取标题、简介、发布时间（`extract_video_meta()`），避免正文过短与日期误判
- 入口：`docker/entrypoint.sh` 统一处理 `run`/`init`/`test`/`smoke`/`live-news`/`api`/`shell`，数据库默认写入 `/app/data/news.db`
- API 服务：`docker compose up -d api` 启动 `news-crawler-api`，端口由 `NEWS_API_PORT` 控制（默认 8000）
- 挂载：`sources.local.json` 只读挂载（私有配置不进镜像）；`./data` 持久化数据库
- 容器以 root 运行，避免跨平台 bind mount 的 UID 权限问题；安全要求高时可改非 root + 命名卷
- 一键部署：`./docker-deploy.sh`（Linux/macOS）或 `.\docker-deploy.bat`（Windows）
- 国内加速：`PIP_INDEX_URL`、`PLAYWRIGHT_DOWNLOAD_HOST` 通过环境变量/构建参数注入

### 9.2 CI

`.github/workflows/deploy-test.yml`：

- 触发：push 到 `master`，或手动 `workflow_dispatch`
- `python-checks` job：Python 3.10–3.13 矩阵，执行 compileall、ruff 关键规则与 smoke
- `shell-and-yaml` job：bash 语法检查、shellcheck 与 YAML 解析校验
- `docker-build` job：Ubuntu，`docker build` 后执行容器内 `smoke`、`api_smoke.py`、compose API 健康检查，并校验裸 `docker run ... api` 的持久化数据库路径
- `docker-build-full` job：默认含浏览器镜像构建 + Chromium 启动校验 + smoke + 实时新闻抓取验证（5 静态 / 3 JS 渲染 / 2 视频，仅 push 或手动运行）
- 通过标准：`python-checks`、`shell-and-yaml`、`docker-build`、`docker-build-full` 的构建与 smoke 步骤必须退出码为 0（包含 `SMOKE TEST PASSED`）
- `live-news` 步骤依赖公开新闻站点，设为 `continue-on-error: true` 仅作观测：失败会在日志中显示但不阻塞 master；本地可用 `--seed` 复现

---

## 10. 测试策略

### 现有测试资产

- `tests/`：约 62 个上游新闻页面 HTML 样本，覆盖 30 个站点目录；
- `test_list_page/`：列表页样本；
- `visiable_test/`：可见性测试样本；
- `smoke_test.py`：部署自检，验证导入、日期解析、GNE 抽取、SQLite 初始化；
- `api_smoke.py`：启动临时 uvicorn + 临时 SQLite，验证鉴权、查询、详情、统计与异步作业全链路；
- `verify_live_news.py`：访问公开新闻站点，验证 5 个静态文本、3 个 JS 渲染与 2 个视频页面的抽取；
- `example.py`：遍历 `tests/**/*.html`，打印抽取结果（人工观察用）。

### 提交前必须执行

```bash
docker compose run --rm --entrypoint python crawler -m py_compile news_crawler.py url_extract.py smoke_test.py verify_live_news.py api.py api_smoke.py news_api
docker compose run --rm crawler smoke
docker compose run --rm --entrypoint python crawler -X utf8 api_smoke.py
```

如果改动了抓取或日期逻辑，至少再执行：

```bash
docker compose run --rm crawler test --config sources.local.json --limit 2
```

### 测试缺口（后续可补）

- 目前没有 pytest 单元测试；
- CI 的 live-news 只做小样本真实抓取，不覆盖所有站点；站点反爬/改版可能导致该 job 波动；
- `content_needs_render()` 的占位判断是启发式规则；
- 日期解析为多正则启发式，缺少系统化用例集。

---

## 11. 已知问题与技术债

按优先级排序：

1. **占位正文判断仍偏启发式**
   `content_needs_render()` 现在规则为：正文为空、或少于 100 字（`MIN_CONTENT_LENGTH`）、或少于 500 字且含加载中关键词。少于 100 字会触发一次浏览器渲染回退；渲染回退后仍少于 100 字时，`extract_article()` 记 WARNING 并照常入库（导航碎片可能因此被当成正文，例如央视的“正在阅读：&lt;标题&gt;”）。渲染回退本身失败时仍返回 `None`（记为 `no_content`，下次重试）。占位词表仍为硬编码，尚未判断占位词出现位置。

2. **入库正文段落换行丢失**
   `process_entry()` 使用 `clean_text()` 压缩所有空白，段落分隔被替换为空格。如果后续要做正文展示或导出，需要保留换行或增加原始正文列。

3. **无法显式强制使用 public 配置**
   `resolve_config_path()` 在存在 `sources.local.json` 时会优先使用它，即使显式传入 `--config sources.json`。建议增加 `--no-local-config` 或显式路径优先级。

4. **日期解析属于多正则启发式**
   不同网站的日期格式可能互相干扰，优先通过站点 `date_xpath` + `date_format` 精确配置；新增格式时必须补充单元测试。

5. **CI 真实抓取覆盖有限**
   `live-news` job 已抽样验证静态、JS 渲染与视频三类真实页面，但网络抓取受站点改版/反爬影响大，仍需补充 mock HTML 单测来覆盖解析逻辑。

6. **MySQL 迁移未实现**
   当前只支持 SQLite；如需 MySQL，需要先抽象存储层，并处理 `ON CONFLICT` → `ON DUPLICATE KEY UPDATE`、URL 唯一索引长度、`DATETIME` 类型等差异。

7. **API 作业队列为单进程**
   `news_api/jobs.py` 使用进程内 `ThreadPoolExecutor`，默认 1 worker；多副本部署会出现任务抢占或重复执行，需要外部队列（如 Redis/Celery）才能水平扩展。

8. **上游测试样本中的误报告警**
   `tests/stcn/1.html` 含上游页面的微信 `appId`，GitHub secret scanning 曾误报，已标记为 false positive。不要因为该告警删除上游测试样本，除非有明确决策。

---

## 12. 多 Agent 协作规范

### 12.1 分支与提交

- 每个 Agent 独立分支：`feat/xxx`、`fix/xxx`、`docs/xxx`、`ci/xxx`；
- 不直接改 `master`，通过 PR 合并；
- 提交信息使用 Conventional Commits：`feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`chore:`；
- 合并前必须通过 CI 和冒烟测试。

### 12.2 建议的模块分工

| 工作流 | 负责范围 | 主要文件 |
| --- | --- | --- |
| A. 抽取算法 | GNE 正文/标题/时间/作者提取优化 | `gne/` |
| B. 抓取调度 | 列表解析、日期过滤、并发、入库 | `news_crawler.py` |
| C. 网络与渲染 | HTTP 抓取、编码、SSL、Playwright 回退 | `url_extract.py` |
| D. 站点适配 | XPath 调优、真实站点接入（配置只放本地） | `sources.local.json` |
| E. 工程化 | Docker 部署、CI、文档、发布 | `Dockerfile`、`docker/`、`docker-compose.yml`、`docker-deploy.*`、`.github/`、`README.md`、`AGENTS.md` |

### 12.3 文件冲突风险

- `news_crawler.py` 是最核心、最易冲突的文件，修改前先同步最新 `master`；
- `url_extract.py` 被 `news_crawler.py` 导入，改函数签名要同步调用方；
- `tests/` 与 `docs/` 是上游资产，除非明确需要，不要批量改动；
- `sources.local.json` 是本地文件，不同 Agent 不要互相覆盖，建议各自保存副本。

### 12.4 共享接口约定

- `fetch_html(url, *, url_validator: Callable[[str], None] | None = None) -> str`：失败时抛出异常；传入 `url_validator` 后每次 HTTP 重定向都会先用新 URL 调用它，抛异常即中断重定向；
- `fetch_html_with_browser(url) -> str | None`：失败返回 `None`；
- `content_needs_render(content: str) -> bool`：True 表示需要浏览器渲染（空、短于 100 字、或含加载占位词）；
- `content_is_too_short(content: str) -> bool`：True 表示正文短于 `MIN_CONTENT_LENGTH`（100 字）；
- `extract_article(url, extractor, *, allow_browser: bool = True, url_validator=None) -> dict | None`：单 URL 完整流程；`allow_browser=False` 时禁用浏览器渲染回退（API 的 extract 默认如此）；
- `GeneralNewsExtractor.extract(html, **kwargs) -> dict`：正文抽取入口；
- `create_app(settings=None) -> FastAPI`：API 应用工厂，测试可注入独立设置；
- `article_id_for(source, url) -> str`：稳定文章 ID，改动会破坏下游引用；
- `POST /v1/jobs` 的 `crawl`/`extract` schema 属于对外契约，新增字段必须保持向后兼容；
- 数据库字段与 status 语义保持向后兼容，新增字段需要迁移策略。

### 12.5 隐私与安全红线

- 不提交任何真实站点配置、Token、Cookie、账号、密码、代理地址；
- 不在日志中打印完整请求头或凭据；
- 抓取频率遵守目标站点条款，合理设置 `delay`；
- 第三方 GitHub 加速代理存在代码被篡改风险，生产环境应核对提交哈希。

---

## 13. 发布流程

1. 确认 `master` 上所有改动已通过 CI；
2. 同步更新版本号：
   - `pyproject.toml` → `version`
   - `gne/__init__.py` → `__version__`
   - `uv.lock` → 项目 package 的 `version`
3. 提交：`release: 升级版本至 x.y.z`；
4. 推送 `master`，等待 `deploy-test` 工作流成功；
5. 打标签并推送：

   ```bash
   git tag -a vX.Y.Z -m "news-crawler vX.Y.Z"
   git push origin vX.Y.Z
   ```

6. 创建 GitHub Release，写清新增功能、修复内容和验证情况。

---

## 14. 快速上手命令（Agent 版）

```bash
# 1. 同步代码
git pull origin master

# 2. 构建镜像（首次或依赖变更后）
docker compose build

# 3. 冒烟测试
docker compose run --rm crawler smoke
docker compose run --rm --entrypoint python crawler -X utf8 api_smoke.py

# 4. 列表解析测试
docker compose run --rm crawler test --config sources.local.json --limit 2

# 5. 小批量抓取
docker compose run --rm crawler run --config sources.local.json --limit-per-source 1

# 6. 启动 API
docker compose up -d api
curl http://localhost:8000/v1/health
```
