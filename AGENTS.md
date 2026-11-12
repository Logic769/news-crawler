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
| 当前正式版本 | `v1.1.2` |
| 开源协议 | GPL-3.0（继承自上游 GeneralNewsExtractor，全文见 LICENSE） |
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
| HTTP 请求 | urllib（标准库） | 带 User-Agent、编码探测；证书校验失败默认报错，仅 `NEWS_INSECURE_SSL` 显式开启时降级 |
| 并发模型 | concurrent.futures.ThreadPoolExecutor | 站点级并发，每个站点内部串行 |
| 时区处理 | zoneinfo | 默认 `Asia/Shanghai` |
| 打包 | hatchling + pyproject.toml | 支持 `pip install -e ".[browser,api]"` |
| 依赖锁定 | uv.lock | 提供给使用 uv 的开发者 |
| HTTP API | FastAPI + Uvicorn | 可选依赖 `[api]`，提供 `/v1` 标准新闻接口与异步作业 |
| CI | GitHub Actions | `deploy-test.yml` 执行多 Python 版本（3.10–3.14）ruff/mypy/离线回归、Docker 构建、容器冒烟；真实新闻抓取仅在 push、夜间与手动触发时运行 |

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
├── smoke_fixture.py                # 冒烟测试共用的内置新闻页（镜像内没有 tests/ 时的回退样本）
├── backfill_raw_content.py         # 回填历史行的 raw_content（重抓 URL，只写换行正文，不动 content）
├── api.py                          # FastAPI 入口（uvicorn api:app）
├── news_api/                       # API 应用：设置、模型、查询、作业队列
├── api_smoke.py                    # API 冒烟测试（本地 HTTP + 临时数据库）
├── verify_live_news.py             # 实时新闻抓取验证（默认抽取 10 静态 / 10 JS / 10 视频）
├── live_news_pool.json             # 实时验证页面池（100 静态 / 50 JS / 50 视频）
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
├── gne/                            # 新闻抽取核心库（本项目重构版）
│   ├── __init__.py                 # 公共入口（GeneralNewsExtractor 与各字段提取器）
│   ├── engine.py                   # 抽取主流程（预处理 → 各字段提取 → 正文）
│   ├── config.py                   # .gne 配置加载（导入时读取一次）
│   ├── constants.py                # 时间/作者/标题正则、默认 XPath 与噪声规则
│   ├── exceptions.py               # NoContentException
│   ├── html.py                     # HTML 预处理、噪声节点移除、图片 URL 补全
│   ├── text.py                     # 最长公共子串与文本归一化
│   ├── utils.py                    # 兼容旧 gne.utils 的 re-export
│   ├── defaults.py                 # 兼容旧 gne.defaults 的 re-export
│   ├── extractors/
│   │   ├── __init__.py             # 导出各字段提取器
│   │   ├── content.py              # 正文密度算法
│   │   ├── title.py                # 标题提取
│   │   ├── time.py                 # 发布时间提取
│   │   ├── author.py               # 作者/来源提取
│   │   ├── meta.py                 # meta 信息提取
│   │   └── list.py                 # 列表页提取（兼容旧 ListPageExtractor）
│   └── extractor/
│       └── __init__.py             # 旧 gne.extractor 路径兼容层
├── tests/                          # 上游新闻页面 HTML 样本（约 62 个文件）
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
process_entry()                 # 单篇文章处理（详情请求同样遵守站点 delay）
        │
        ├── fetch_article_html()        # 直接抓取；仅 401/403/406/429 才回退浏览器
        ├── extract_article()           # GNE 抽取，正文为占位时回退浏览器
        └── save_article()              # 写入 SQLite（upsert）
```

### 模块职责速查

| 函数 | 位置 | 职责 |
| --- | --- | --- |
| `fetch_html` | `url_extract.py` | 标准库 HTTP 抓取、gzip 解压、编码探测、SSL 兼容（上下文复用） |
| `fetch_html_with_browser` | `url_extract.py` | Playwright 无头 Chrome 渲染；浏览器按线程复用，`close_thread_browser()` 释放 |
| `normalize_images` | `url_extract.py` | 把 `//cdn/x.jpg` 这类相对图片地址补全为绝对 URL |
| `should_skip_stored` | `news_crawler.py` | 已入库条目是否可跳过（`old` 在窗口放宽后允许重抓） |
| `content_needs_render` | `url_extract.py` | 判断正文是否为空或只是“正在加载”占位 |
| `extract_article` | `url_extract.py` | 单 URL 完整流程：抓取 → GNE → 必要时渲染 |
| `parse_datetime` | `news_crawler.py` | 解析中文/数字/相对日期，返回带时区 datetime |
| `parse_list_items` | `news_crawler.py` | 用站点配置的 XPath 解析列表页 |
| `process_entry` | `news_crawler.py` | 单篇文章的抓取、过滤、抽取、入库（项目申报站点分派到 `_process_project_entry`） |
| `_process_project_entry` | `news_crawler.py` | 项目申报条目：只读列表字段入库，按 `deadline` 判 `old`，按 `content_required` 决定正文要求 |
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

列表参数：`status`、`source`、`published_from`、`published_to`、`deadline_from`、`deadline_to`、`q`、`page`、`page_size`、`order`（`published_desc`/`published_asc`/`updated_desc`/`deadline_desc`/`deadline_asc`）、`include_content`（为 `true` 时列表返回 `content`；`raw_content` 无论何时都只在详情返回）。项目申报行 `publish_time` 为空，使用 `published_from`/`published_to` 会被排除，应按 `deadline` 过滤（`deadline` 为站点墙钟，`deadline_*` 带时区偏移时只取墙钟部分、不做换算；`deadline_*` 与 `published_*` 同时传会 AND 成空集）。文章列表与作业列表返回 `{"data": [...], "meta": {...}}`；文章详情、统计、健康检查返回对应对象；`/v1/sources` 返回 `{"data": [...]}`。

设置 `NEWS_API_KEY` 后，除 `/v1/health` 与文档外都需 `X-API-Key` 或 `Authorization: Bearer <key>`。作业状态为 `queued`/`running`/`succeeded`/`failed`，单进程执行，避免 SQLite 并发写冲突。排队 + 运行中的作业上限为 `NEWS_MAX_PENDING_JOBS`（默认 50），超出返回 `429`；相同类型与参数的未完成作业复用同一 ID。`extract` 默认拒绝回环、内网与保留地址（`NEWS_ALLOW_PRIVATE_URLS=1` 可放开）；`fetch_html` 通过自定义 `HTTPRedirectHandler` 对每个重定向目标复用同一校验，默认还以 `allow_browser=False` 禁用浏览器回退（`NEWS_EXTRACT_ALLOW_BROWSER=1` 可放开），避免页面 JS 绕过地址限制。上述限制仍不能替代 API Key。已完成作业保留 30 天后自动清理。未设置 `NEWS_API_KEY` 时默认开放（仅提示 warning）；设置 `NEWS_REQUIRE_AUTH=1` 后行为是"未配置 key 则业务请求一律 401，已配置 key 时按 key 校验"（401 带 `WWW-Authenticate`，`/v1/health`、`/docs`、openapi 与根路径始终开放）。错误响应统一为 `{"error": {"code", "status": <HTTP 码>, "message", "details"?}}`，参数校验错误为 422 且带 `details`。`docker compose` 通过白名单透传 API 变量，需在 `.env` 或 shell 中设置后生效。

---

## 6. 站点配置说明

真实站点配置放 `sources.local.json`，不要提交到 Git。

```json
{
  "workers": 16,
  "days": 3,
  "timezone": "Asia/Shanghai",
  "delay": 0.3,
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
| `list_urls` | 二选一 | 多个固定列表页地址（旧版分页方式；单 `list_url` 站点优先用 `pagination`） |
| `item_xpath` | 是 | 每条新闻节点的 XPath |
| `title_xpath` | 否 | 默认第一个 `a` 的文本 |
| `url_xpath` | 否 | 默认第一个 `a` 的 `href` |
| `date_xpath` | 否 | 默认从整条节点文本中自动识别日期 |
| `date_format` | 否 | 指定 `strptime` 格式 |
| `article_title_xpath` | 否 | 详情页标题 XPath（`<title>` 是站点名、真标题在正文 h1 时使用）；推荐直接写标题元素（如 `//h1`），会取其全部文本（含 `<em>` 等子元素）；`/text()` 只取第一个直接文本节点，标题含子标签时会截断；以 `string(` 开头的表达式会被忽略 |
| `article_publish_time_xpath` | 否 | 详情页发布时间 XPath；推荐直接写日期元素（如 `//p[contains(@class,'article__subTitle')]/span[1]`）；以 `string(` 开头或非法表达式会被忽略 |
| `article_body_xpath` | 否 | 详情页正文容器 XPath，透传 GNE 的 `body_xpath`；必须是元素节点表达式，属性/文本表达式（`//div/@id`、`//div/text()`）、未命中、命中空容器都会记 WARNING 并回退整页 `//body`（见 `ContentExtractor._resolve_body`）；命中多个节点只取第一个并记 WARNING；以 `string(` 开头或非法表达式在加载配置时被忽略；正文与配图仍在该容器内按密度算法取最佳子块（并列多个正文块时可能只取一段），收窄容器会漏掉容器外的配图；视频页分支优先于本字段 |
| `date_skew_days` | 否 | 详情日期与列表日期允许相差的天数，默认 7，范围 0–365；超过时改用列表日期 |
| `listing_type` | 否 | `news`（默认）或 `project_application`。项目申报模式把列表页当作数据源，见下方“项目申报列表” |
| `deadline_xpath` | 否 | 项目申报：截止时间单元格 XPath（相对条目节点），写 `deadline`/`deadline_raw`，不写 `publish_time` |
| `deadline_format` | 否 | 项目申报：截止时间格式，默认回退 `date_format` |
| `project_status_xpath` | 否 | 项目申报：申报状态单元格 XPath |
| `content_required` | 否 | 项目申报：是否必须抓到详情正文；默认 `false`（登录墙/短正文仍记 `ok`，只存列表字段），`true` 退回新闻语义 |
| `pagination` | 否 | 自动分页配置对象，见下方“自动分页”；只对单个 `list_url` 生效，配置 `list_urls` 时仍按固定地址列表抓取 |
| `timezone` | 否 | 默认 `Asia/Shanghai` |
| `render` | 否 | 列表页是否强制 Chrome 渲染 |
| `delay` | 否 | 请求间隔秒数，默认 0.3；列表页与文章详情页请求前都会等待 |

### 自动分页

`pagination` 仅作用于单个 `list_url`；配置了 `list_urls` 的站点保持旧的固定地址列表行为（不受 `max_pages`/`stop_when_older_than_cutoff` 影响）。默认 `mode=none`。

```json
"pagination": {
  "mode": "template",
  "url_template": "https://example.com/news/index_{page}.html",
  "total_pages_regex": "index_(\\d+)\\.html",
  "start": 2,
  "max_pages": 50,
  "stop_when_older_than_cutoff": true,
  "stop_after_known_pages": 0
}
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `none` | `none` 只抓 `list_url`；`template` 按 `url_template` 生成后续页；`next` 跟随“下一页”链接；`auto` 有模板用模板，否则跟随下一页 |
| `url_template` | 空 | 页码模板，必须包含 `{page}`；缺少占位符时 `template` 模式回退为只抓首页，`auto` 模式改走下一页 |
| `last_url_xpath` | 空 | 尾页链接 XPath；命中元素时读取 href/onclick/data-page/value 和文本作为候选，最终取最大页码（`data-page="12"` 这类纯数字直接作为页码）；不填时从整页 HTML 用 `total_pages_regex` 探测 |
| `next_url_xpath` | 空 | “下一页”链接 XPath；支持相对地址与常见 `onclick=location.href=...` 写法 |
| `total_pages_regex` | `(?:index_\|page[=/])(\\d+)` | 提取最大页码的正则，捕获组必须是数字（表格中 `\\|` 表示正则里的或） |
| `start` | `1` | 模板模式从第几页开始生成后续页（第 1 页始终是 `list_url`，通常设 2）；范围 1–1000 |
| `max_pages` | `50` | 最多抓取页数（含 `list_url` 计 1 页，后续页最多 `max_pages-1` 个）；范围 1–500 |
| `stop_when_older_than_cutoff` | `true` | 当前页最晚日期早于抓取窗口起点时停止翻页（整页每条都能解析出日期且都早于窗口才停，避免置顶旧文或缺失日期导致漏抓）；项目申报模式不按发布日期早停，窗口判断只看 `deadline` |
| `stop_after_known_pages` | `0` | 连续 N 页链接都已入库时停止（增量运行）；范围 0–100，默认 0 关闭；仅 template/next/auto 模式生效。开启后若上次运行更后页失败，可能提前停止，建议确认完整抓取后再开 |

未配置 `render: true` 时，列表页解析为空会自动用 Chrome 渲染一次，并基于渲染后的 DOM 重新解析与继续翻页。自动分页在页内按 URL 去重；`test` 命令输出页数与停止原因（`cutoff`/`known_pages`/`no_next`/`max_pages`/`empty`/`fetch_failed`/`done`）。实现入口：`collect_list_entries_detailed(source, *, cutoff=None, known_urls=None) -> dict`，返回 `items/pages/page_urls/stop_reason`；`collect_list_entries` 保留旧返回类型。

自动分页会为列表页启用 SSRF 地址白名单：配置的 `list_url`/`list_urls` 是唯一可信目标，且要求 scheme + host + port 完全一致；其他目标必须是公网地址，并且只允许 http 80 / https 443 端口。指向配置主机其他端口的链接会被拒绝；同端口无法按路径区分，配置主机本身视为可信。带地址校验的列表页请求为直连（不读取 `HTTP(S)_PROXY`），文章详情页保持原有代理行为。浏览器回退时每个请求都会重新校验，但 Playwright 自行解析 DNS，无法像 HTTP 路径那样在连接层钉扎 IP，残余 DNS rebinding 风险需由出口网络策略兜底。

### 项目申报列表

`listing_type: project_application` 用于项目申报公告类站点：列表页本身即完整数据（标题、截止时间、申报状态、详情链接），详情页常常需要登录。行为差异：

- `deadline_xpath` 解析的截止时间写 `deadline`/`deadline_raw`，不写 `publish_time`；列表自身的发布日期按 `date_xpath` 正常写入 `publish_time`/`publish_time_utc`/`list_publish_time`（`publish_time_source=list`），与 deadline 完全解耦。未配 `date_xpath` 的列表项目行 `publish_time` 为空、按发布时间排序会排在末尾；`/v1/articles` 支持 `order=deadline_desc|deadline_asc` 与 `deadline_from`/`deadline_to`，`published_from`/`published_to` 只命中配置了 `date_xpath` 的项目行；
- `title` 与 `list_title` 都写列表标题，`title_source=list_title`（详情页 `<title>` 通常是站点名，不可信）；列表标题经 `clean_title()` 清洗，属性值里的字面量 `<br>`/`<p>` 会被删掉（新闻站点同样清洗；标签名后的属性窗口不限长，带长 `style` 的标签整段删除、不留半截开标签；纯文本清洗的已知取舍：标题正文本身在讨论标签写法时同样会被删，且「`<` + 标签名 + 较远 `>`」形状的普通文本（如「当 a <b 时，成本 > 收入」）也会被整段删除）；
- `content_required` 默认 `false`：详情抓取失败、或正文短于 100 字（登录墙占位）时只保存列表字段并记 `ok`，下次运行按 `ok` 跳过；设为 `true` 则按新闻语义记 `no_content`/`error` 并重试；
- 截止时间早于抓取窗口起点时记 `old`（用 `--force` 可强制重抓），避免每次重跑历年公告；已入库 `ok`/`old` 行会在后续运行中按列表刷新 `title`/`list_title`/`title_source`/`deadline`/`deadline_raw`/`project_status`，不抓详情；原 `old` 行截止时间原文非空且变为不可解析（如“常年受理”）时恢复 `ok`，`content_required=true` 时继续抓正文（截止单元格为空时保留旧值与 `old`）；同一 URL 从新闻切项目时，未配 `date_xpath` 的站点会清空 `publish_time*`，配了 `date_xpath` 的站点改写成列表日期并把 `publish_time_source` 归一为 `list`（值相同但来源仍是历史详情来源的行也会被纠正）；本次列表日期解析失败则沿用旧值，此时旧值若来自新闻模式的详情日期，来源保持原值并记 WARNING，要清空需临时移除 `date_xpath` 重抓；若该行连可沿用的本地墙钟都没有（`publish_time` 为空），`publish_time_source`/`publish_time_raw`/`list_publish_time` 一并清空；两种情况都保留 `content`/`raw_content`/`author`/`images`；无年份截止时间按列表发布日期（缺失时当前时间）取最近合法年份（列表状态为已截止时以列表发布日期为下界、抓取时刻为上界，取区间内最近的合法年份（如 2025-12-20 发布、原文“1月15日”、2026-02-01 抓取 → 2026-01-15；列表发布日期缺失时只约束上界；区间内无候选时 `deadline` 留空、仅保留原文）；否则取不早于该日期；重抓历年已截止公告时无年份截止时间可能随抓取年份后移，属“取最近合法年份”的已知取舍）；
- 项目模式可以不配 `date_xpath`；此时 `parse_list_items` 保持 `parsed=None`，项目行 `publish_time` 为空。配置了 `date_xpath` 则列表发布日期照常入库，供 `published_from`/`published_to` 过滤。两种情况都不会把截止时间当发布时间；配置了 `date_xpath` 但单元格文本无法解析（如“待定”）时，项目行**不会**回退去解析 URL 里的日期路径（`parse_list_items` 的 `parsed is None and not is_project` 条件），避免把链接中的 `/2025/11/01/` 当发布日期写进 `publish_time` 并覆盖已有的有效列表日期；新闻模式仍保留该 URL 回退。

```json
{
  "name": "svc20_stcsm",
  "list_url": "https://svc20.stcsm.sh.gov.cn/project",
  "listing_type": "project_application",
  "item_xpath": "//tr[contains(@class,'ant-table-row')]",
  "title_xpath": ".//a[contains(@class,'detailLink')]",
  "url_xpath": ".//a[contains(@class,'detailLink')]/@href",
  "deadline_xpath": "./td[2]",
  "deadline_format": "%Y-%m-%d %H:%M:%S",
  "project_status_xpath": "./td[3]",
  "render": true,
  "delay": 0.8
}
```

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
    publish_time  TEXT,            -- 统一为 YYYY-MM-DD HH:MM:SS（站点本地墙钟，供展示/排序）
    publish_time_utc TEXT,         -- 真实时刻 UTC 墙钟串，供跨时区时间过滤；缺 timezone 的历史行为 NULL
    publish_time_source TEXT,      -- 发布时间来源：xpath/labeled_text/meta/text/video_meta/detail/list
    publish_time_raw TEXT,         -- 详情页抽取到的发布时间文本（XPath 场景可能是日期子串）
    list_publish_time TEXT,        -- 列表页给出的日期，用于与详情日期交叉校验
    title_source TEXT,             -- 标题来源：article_title_xpath / gne_default / list_title（项目申报）
    deadline      TEXT,            -- 项目申报截止时间 YYYY-MM-DD HH:MM:SS；新闻站点 NULL
    deadline_raw  TEXT,            -- 项目申报截止时间原文
    project_status TEXT,           -- 项目申报状态文本（申报中/已截止）；新闻站点 NULL
    timezone      TEXT,            -- 写入时的站点时区，历史行可能为空
    author        TEXT,
    content       TEXT,            -- 正文纯文本（clean_text 归一化，段落换行被压成空格）
    content_length INTEGER,        -- 正文长度缓存；列表接口避免读整段正文 blob
    raw_content   TEXT,            -- GNE 原始正文，保留段落换行；仅展示/导出用
    images        TEXT,            -- JSON 数组字符串；NULL 表示本次未提供（保留旧值），[] 表示明确无图（清空）
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

索引：`source`、`status`、`publish_time`、`article_id`（唯一），以及五条查询性能覆盖索引：`(status, publish_time DESC, source, url)`、`(status, updated_at DESC, source, url)` 供列表分页、`(source, publish_time DESC, updated_at DESC, timezone, status)` 供 `/v1/sources` 聚合、`publish_time_utc` 供真实时刻过滤、`deadline` 供项目申报截止时间过滤/排序；`crawl_jobs` 的 `status`、`created_at`、`(type, payload, status)` 去重索引。

旧库升级由 `init_db()` 按 `PRAGMA user_version` 一次性迁移（当前 `SCHEMA_VERSION = 8`，在 `BEGIN IMMEDIATE` 事务内）：`ALTER TABLE` 补 `article_id`、`timezone`、v4 的 `raw_content`（只补列，原文已不可得，历史行不回填）、v5 的 `content_length`（补列后按 `length(content)` 回填 NULL 行）、v6 的 `publish_time_utc`（补列后按每行 `timezone` 换算 UTC 回填，供混时区按真实时刻过滤）、v7 的 `publish_time_source`/`publish_time_raw`/`list_publish_time`/`title_source`（只补列，历史行为 NULL）与 v8 的 `deadline`/`deadline_raw`/`project_status`（只补列，新闻站点为 NULL），按 rowid 键集分页（每批 2000 行）回填 `article_id`，不边读游标边 UPDATE 同表；再补建唯一索引、去重索引与五条性能覆盖索引（`CREATE INDEX IF NOT EXISTS`，写在 `_PERFORMANCE_INDEX_SQL` 并由迁移事务执行，老库升级与新建库结果一致；`SCHEMA_SQL` 里不放这些，因为 `executescript` 会隐式提交，DDL 会落到 `BEGIN IMMEDIATE` 之外）。迁移早退条件校验十二列齐全且 `required_indexes`（五条性能索引 + `idx_articles_article_id` + `idx_crawl_jobs_dedupe`）齐全，完成后置 `user_version`，后续启动直接跳过。首次切换到 `journal_mode=WAL` 前先读当前模式，已是 WAL 则跳过，并对并发首启做短重试（该 PRAGMA 不受 `busy_timeout` 保护）。`crawl_jobs` 中已完成的作业由 `JobManager.start()` 清理 30 天前的记录。

### 7.2 status 语义

| status | 含义 | 是否会被后续运行跳过 |
| --- | --- | --- |
| `ok` | 抓取成功，且在时间范围内 | 是 |
| `old` | 已确认超出时间范围 | 是；但放宽 `--days`/`--since` 让窗口前移后会重新抓取 |
| `no_content` | 正文抽取失败 | 否，会重试 |
| `unknown_date` | 无法解析日期 | 否，会重试 |
| `error` | 抓取或处理异常 | 否，会重试 |

> `listing_type=project_application` 的站点按`deadline`（截止时间）判断是否超出窗口：截止时间早于窗口起点的条目记 `old`；详情页抓不到正文时按 `content_required` 决定记 `ok`（默认，列表字段完整）还是 `no_content`/`error`。

### 7.3 注意事项

- `publish_time` 在 `process_entry()` 中统一格式化为 `YYYY-MM-DD HH:MM:SS`；
- 用旧版本抓取过的数据可能仍是中文日期格式，需要 `--force` 重抓或重建数据库；
- `content` 入库前会被 `clean_text()` 压缩空白，段落换行会丢失；GNE 原始换行正文另存 `raw_content`（代价是正文体积约翻倍，仅 `ok`/`unknown_date` 写入，`old`/`error` 不写）；v4 之前入库的历史行需要 `--force` 重抓，或用 `backfill_raw_content.py` 批量回填（只 `UPDATE raw_content`，不改 `content`/`status`/`updated_at`）；
- 同一 `(source, url)` 使用 upsert，不会产生重复记录；
- `save_article()` 是**部分更新**：空字符串表示“这次没拿到”，不会覆盖库中已有值，因此抓取失败或重新分类时只更新 `status`/`error`，已成功入库的正文不会被清空；`images` 例外地区分三态：`None` 保留旧值、`[]` 明确清空、非空列表覆盖；抓取流程用 `normalize_images_for_update` 把"抽取不到图"归一为 `None`（保留旧值），只有显式传 `[]` 才清空，避免成功重抓因抽取失败抹掉历史图片；
- `timezone` 随文章落库，读取时优先使用该列，旧行（NULL）才回落到当前配置推断；`publish_time_utc` 存真实时刻（UTC），`published_from`/`published_to` 按它过滤以消除混时区偏差，`publish_time_utc` 为 NULL/空串的历史行回退旧的 `publish_time` 墙钟比较；列表排序仍按 `publish_time` 墙钟；
- 详情页日期晚于当前时间超过 1 天时视为不可信（不再自动减一年），回退列表日期；列表日期也缺失时记 `unknown_date`；`date_skew_days` 范围为 0–365；GNE 文本扫描可能只抽到“09月30日”这类无年份日期：主抓取路径优先用列表日期的年份补齐，列表日期也缺失时记 `unknown_date`；API 单 URL extract 无列表日期可交叉校验时同样记 `unknown_date`，不按当前年猜测；
- `old` 分支的 `publish_time_source` 取决于判定时机：列表阶段就确认超出窗口时记 `list`；详情抓取后才发现超出窗口时保留详情来源（`xpath`/`labeled_text`/`meta`/`text`）。`publish_time_raw` 与当前 `publish_time` 同步：回退到列表日期时清空，不再保留旧详情原文；`unknown_date` 本次没有可信发布时间，新插入行会保留本次抽取文本便于排查，已有行则保留旧值与旧时间配对；新行的 `publish_time_source` 为空字符串，已有行保留旧来源，避免旧时间挂新来源；
- 项目申报行（`listing_type=project_application`）的 `title`/`list_title` 都写列表标题（经 `clean_title()` 清掉属性值里的字面量 HTML 标记：`br`/`p`/`div`/`span`/`strong`/`em`/`sub`/`sup` 带不带属性都删；单字母 `b`/`i`/`u`/`a` 只删成对写法与带属性/自闭合/孤立结束标签，孤立的 `<b>` 按普通文本保留以免误伤 `A <B> C`；其余标签原样保留）、`title_source=list_title`；`deadline` 列存截止时间，与发布时间完全解耦：列表页自身的发布日期在配置了 `date_xpath` 时照常写 `publish_time`/`publish_time_utc`/`list_publish_time`（`publish_time_source=list`），未配置时 `publish_time` 为空，两种情况都不会拿截止时间顶替。`publish_time` 为空的项目行按 `published_desc` 排序会排在末尾、且会被 `published_from`/`published_to` 排除；查询项目公告请用 `order=deadline_desc|deadline_asc` 与 `deadline_from`/`deadline_to`。`content_required=false` 时详情抓不到正文也记 `ok`，不会反复重试；同一 URL 从新闻模式切到项目模式时，未配 `date_xpath` 的站点清空 `publish_time*`、配了 `date_xpath` 的站点改写成列表日期并把 `publish_time_source` 归一为 `list`（本次解析失败沿用旧值、来源保持原值并记 WARNING），`content`/`raw_content`/`author`/`images` 保留（可能残留旧新闻正文，检索侧请以 `title_source=list_title` 或站点配置识别项目行，不要只按 `deadline`；`deadline_from`/`deadline_to` 只对有可解析截止时间的项目行生效）；反向切换（项目→新闻）会保留旧 `deadline`/`deadline_raw`/`project_status`，这类新闻行仍可能命中截止时间过滤，查询侧请按站点/字段区分行类型；
- `article_id` 由 `sha256(source + \x1f + url)` 派生，不随数据库重建变化，可安全作为下游引用；
- SQLite 启用 WAL 与 `busy_timeout=30000`（`DB_BUSY_TIMEOUT_MS`，与连接 timeout 一致，不用 5s 覆盖 30s 连接超时），API 作业默认单 worker 写入；只读查询使用 `file:...?mode=ro` URI（路径会转义），WAL 库仍要求目录可写（需要 -shm/-wal）。

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
| `--limit-per-source` | 每个站点最多处理多少条（只计实际处理的条目；已入库被跳过的条目不占名额），调试用 |
| `--force` | 忽略已抓记录，强制重抓 |

> **内存提示**：`--workers` 是站点级线程数；开启浏览器回退时每线程可能各自启动一个 Chromium（约 200–300 MB）。`workers=16` 建议至少 4 GB 可用内存；小内存机器或 JS 站点较多时请调低，否则可能被 OOM Kill。

### 单 URL 抓取

```bash
docker compose run --rm --entrypoint python crawler -X utf8 url_extract.py "https://example.com/article.html"
```

### 回填 raw_content（历史行）

v4 之前入库的文章 `raw_content` 为空。回填脚本只重抓这些行、只写 `raw_content`，不改变 `content`、`status` 与 `updated_at`；抓取失败的行原样保留，可重复运行（已回填的行不再是候选）：

```bash
# 先看有多少条候选（不抓取）
docker compose run --rm --entrypoint python crawler -X utf8 backfill_raw_content.py --db /app/data/news.db --dry-run
# 真正回填（默认 --delay 0.5 控制频率；--source / --limit 可缩小范围；--allow-browser 才回退 Chromium）
docker compose run --rm --entrypoint python crawler -X utf8 backfill_raw_content.py --db /app/data/news.db --limit 50
# 第二次运行：读上一次的失败清单，跳过死链，避免它们反复占用 --limit 名额
docker compose run --rm --entrypoint python crawler -X utf8 backfill_raw_content.py --db /app/data/news.db --limit 50 --skip-file /app/data/backfill_failed.txt
```

- 抓取失败与「UPDATE 未命中」分开统计：后者通常是另一个进程已并发回填或行已被删除，计入「跳过」而不是「失败」；
- 失败明细默认写入与 `--db` **同目录**的 `backfill_failed.txt`（TSV：source / url / error），容器内即 `/app/data/backfill_failed.txt`，随 `./data` 挂载保留；`--failed-list` 可改路径（不能指向数据库文件或目录，否则返回 2）。每次运行都会覆盖该文件，但会保留本次 `--skip-file` 清单中的条目（第三列写「上次失败，本次跳过」），避免把仍然有效的死链记录洗掉；清单为空且全部成功时只剩表头；
- `--skip-file` 读取该清单并把对应 `(source, url)` 从候选中排除；清单不存在时报错退出（返回 2），避免拼错路径后静默失效；
- `--config` 指定站点配置路径（默认 `sources.json`，存在 `sources.local.json` 时由 `resolve_config_path` 优先取它）。回填会按站点名读取 `article_body_xpath` 并透传给 GNE，否则正文容器被页面长文本块带偏的站点会把诱饵写进 `raw_content`，与同一行的 `content` 不一致；`--dry-run` 不读配置；一个都没读到时打印提示（常见于未挂载 `sources.local.json`、只读到镜像内空模板）。

脚本的 `--db` 默认值取自 `news_crawler.DEFAULT_DB_FILE`（相对路径 `news.db`），容器内必须显式传 `/app/data/news.db`，否则会指向 `/app/news.db`。

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

- 触发：push 到 `master`、PR、夜间定时（UTC 18:00 / 北京时间 02:00）或手动 `workflow_dispatch`
- `python-checks` job：Python 3.10–3.14 矩阵，执行 compileall、`ruff check .`、`mypy`（仅 3.12 跑一次）、smoke、api_smoke 与 62 篇样本的离线回归
- `shell-and-yaml` job：bash 语法检查、shellcheck 与 YAML 解析校验
- `docker-build` job：Ubuntu，`docker build` 后执行容器内 `smoke`、`api_smoke.py`、compose API 健康检查，并校验裸 `docker run ... api` 的持久化数据库路径
- `live-news` job：只在 push `master` 时运行（`if: github.event_name == 'push'`），安装 `[browser,api]` 与 Chromium 后用宿主机 Python 执行 `verify_live_news.py --static 10 --dynamic 10 --video 10`；这是**硬性门槛**，抽取数量不足即 job 失败。PR 上不跑：失败多来自站点改版/反爬而非本次改动，且 PR 已有离线回归兜底；夜间定时与手动触发改由 `docker-build-full` 在镜像内跑同一组 10/10/10，避免重复抓取
- `docker-build-full` job：默认含浏览器镜像构建 + Chromium 启动校验 + smoke + 镜像内同类实时新闻验证（10 静态 / 10 JS 渲染 / 10 视频）；只在夜间定时与手动触发时运行（`if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'`），避免与 push 时的宿主机 `live-news` 重复抓取同一组页面
- `concurrency` 按「工作流 + 分支 + 事件类型」分组：夜间任务不会被 push 取消（`cancel-in-progress` 仅对非 schedule 为 true），保证站点改版的夜间信号不被白天的提交覆盖
- `notify-nightly-failure` job：夜间定时任务失败时自动开/更新标题为「[nightly] 夜间回归失败」的 issue（`permissions: issues: write`）
- 通过标准：`python-checks`、`shell-and-yaml`、`docker-build` 必须为 0；`live-news` 在非 PR 事件上必须为 0；`docker-build-full` 在夜间定时与手动触发时必须为 0（两者分别需打印 `SMOKE TEST PASSED` / `LIVE NEWS VERIFICATION PASSED`）
- 手动触发时可用 `live_news_seed` 输入固定随机种子，便于复现某次失败的抽样

---

## 10. 测试策略

### 现有测试资产

- `tests/`：约 62 个上游新闻页面 HTML 样本，覆盖 31 个站点目录；
- `smoke_test.py`：部署自检，验证导入、日期解析、GNE 抽取、SQLite 初始化、视频页判定、图片地址补全、浏览器回退状态码白名单、性能索引是否建齐、`raw_content` 落库与「本次未传不覆盖」、`old` 在窗口放宽后是否重抓；样本缺失时回退到 `smoke_fixture.py` 的内置新闻页（与 `api_smoke.py` 共用）；
- `api_smoke.py`：启动临时 uvicorn + 临时 SQLite，验证鉴权、查询、详情（含 `raw_content` 换行）、统计、异步作业全链路，以及入库图片必须为绝对 URL；
- `verify_live_news.py` + `live_news_pool.json`：从 100 静态 / 50 JS 渲染 / 50 视频的页面池中随机抽取（默认 10/10/10）访问公开新闻站点，验证三条抽取路径；视频池要求覆盖 ≥10 个来源站点；
- `regression/`：62 篇样本的离线抽取基线（含 `meta_keys`/`meta_sha1`/`is_video`），外加列表页解析、视频页判定与回填脚本三组独立用例；

### 实时新闻池维护

`live_news_pool.json` 是 CI 抽样的页面池，格式为 `{"static": [...], "dynamic": [...], "video": [...]}`：

- 池文件缺失或某类为空时，`verify_live_news.py` 回退到内置的小样本池，并打印 `POOL SIZES`；
- 抽样用 `--seed` 决定；单页失败会从剩余池补抽（日志 `REPLACED PAGES`，预算为 `count * 3`），补不足数量则失败；
- 站点改版导致的失效页面必须从池中剔除：本地复现失败后确认该 URL 确实无正文，再从 JSON 中删掉并补入同类的健康页面；
- 校验单个 URL 可用：

  ```bash
  docker compose run --rm --entrypoint python crawler -X utf8 -c "
  from gne import GeneralNewsExtractor
  import verify_live_news as v
  print(v.check_static('https://example.com/a.html', GeneralNewsExtractor()))"
  ```

  `GeneralNewsExtractor` 实例可跨线程共享：`extract()` 每次调用内部新建 `ContentExtractor`，不使用实例级可变缓存；批量校验可复用同一个实例；
- 池子生成脚本与探测结果保存在本地 `backups/pool_build/`（被 Git 忽略），不属于仓库内容。

### 离线回归基线（regression/）

`tests/` 下的 62 个 HTML 样本已接入离线回归，基线随仓库发布，CI 每次运行都会比对：

| 文件 | 作用 |
| --- | --- |
| `regression/baseline.json` | 基线快照，62 个样本的 title/author/publish_time/content_len/content_sha1/images/meta_keys/meta_sha1/is_video/error，含 `gne_version` 与 `sample_count` |
| `regression/baseline_utils.py` | 抽取单个样本的稳定指纹（正文取 sha1 而非全文；meta 存键列表 + 排序后 JSON 的 sha1，避免基线文件膨胀） |
| `regression/generate_baseline.py` | 重新生成基线：`python -X utf8 -m regression.generate_baseline`（必须用 `-m`；直接跑脚本时 `sys.path` 指向 `regression/`，`from regression.baseline_utils import ...` 会失败） |
| `regression/test_gne_regression.py` | 3 个用例：全部样本与基线一致、基线可复现、样本集合与基线匹配 |
| `regression/test_list_page.py` | 7 个用例：列表页解析的条目定位、feature 缺失、少于 4 条时不回退、`count()`/`number()`/`boolean()` 等标量 XPath 在 `xpath_text`/`xpath_href` 中返回空而不崩溃 |
| `regression/test_video_detection.py` | 7 个用例：路径规则、`og:type`、JSON-LD `VideoObject`、文章内嵌视频不误判 |
| `regression/test_backfill.py` | 15 个用例：候选 skip/limit（含真实 SQLite）、LIMIT 下推与游标提前停止、游标异常关闭、失败清单列清洗、BOM 跳过清单、`--skip-file` 条目在下次失败清单中保留、`--failed-list` 指向数据库/目录/硬链接/符号链接/未创建的 WAL·SHM 侧车路径时抓取前返回 2、正常清单文件不误杀 |
| `regression/test_time_extractor.py` | 34 个用例：标签日期优先于正文、XPath 元素/字符串/多值、多日期按文本位置取最早、无效 ISO 候选跳过、脚本与页脚排除、兄弟/嵌套行内节点拼接、块级边界、表格单元格（`td`/`th`）标签日期与不跨 `<tr>`、停止词、dateUpdate 兜底、拆分年份重建 |
| `regression/test_parse_datetime.py` | 16 个用例：中文/数字/相对日期、ISO 偏移、未来日期与年份调整、reference_year 指定年份、无年份日期按基准年补齐、跨年无年份日期取最近合法年份、闰日落非闰年返回 None |
| `regression/test_publish_source.py` | 9 个用例：GNE 内部来源映射到落库值、视频页 meta 来源标记、`clean_title()` 清掉标题里的字面量 HTML 标签（`<br>`/`<BR/>`/`<b>`/`<p>`）且不误伤 `a < b > c` 这类普通文本、带长 `style` 属性的标签整段删除不留半截开标签、纯文本标题原样保留、单字母标签只在成对/带属性/自闭合/孤立结束这几种无歧义写法下清除（`A <B> C` 保留）、`parse_list_items` 清洗属性标题、`process_entry` 清洗详情标题 |
| `regression/test_body_xpath.py` | 21 个用例：默认密度算法被诱饵长文本带偏、指定 `article_body_xpath` 选中真正文、未命中时回退整页 `//body` 不抛异常、`extract_from_html` 透传与缺省空串、`load_config` 保留合法值、非法 `//div[` 与 `string(...)` 被忽略并告警、`process_entry` 与 API extract 作业转发、`news_crawler.extract_article` 直调转发、`url_extract.extract_article` 真实 GNE 端到端、浏览器回退第二次 extract 带 `body_xpath`、`_resolve_body` 对属性/文本表达式/未命中/空容器回退 `//body` 且告警、命中容器正常使用、`load_source_xpaths` 读配置与缺配置、`backfill` 转发 `body_xpath` |
| `regression/test_process_entry_dates.py` | 17 个用例：未来/超 skew 详情日期回退列表、跨 UTC 日期边界、无年份按列表年补齐、跨年无年份日期取上一年并保留时刻、跨年正方向（列表年末+详情次年）保留时刻、未来无年份日期按列表年补齐、无列表日期记 unknown_date、title_source 归一、skew 钳制、unknown_date 不覆盖旧来源 |
| `regression/test_extract_job_dates.py` | 7 个用例：API extract 作业超 skew 回退库内列表日期、无年份兜底、跨年无年份取上一年、跨年正方向保留时刻、详情不可解析兜底、无列表日期记 unknown_date |
| `regression/test_project_application.py` | 56 个用例：`listing_type` 默认值/非法值回退、`deadline_xpath`/`project_status_xpath` 解析且不把截止时间当发布时间、详情缺失或登录墙短正文仍存列表字段（标题取列表标题、`title_source=list_title`）、`content_required=true` 走 `no_content`、截止时间早于窗口记 `old`、v7→v8 迁移补三列、deadline 不回退发布时间、不可解析截止时间保留原文、项目 `old` 行二次运行 skip、`--force` 仍抓详情、长正文成功入库、`content_required` 字符串值归一、deadline 过滤与排序、已入库 `ok`/`old` 行刷新列表字段（`title`/`list_title`/延期/截止/不可解析清空）且不抓详情、`old` 恢复 `ok` 且 `content_required=true` 时继续抓正文、新闻切项目清空 `publish_time*`、无年份未来截止保留当年、无年份 `deadline_format` 按基准年补齐、闰日隔两年取最近合法年、deadline 边界按站点墙钟、无分隔符 `deadline_format`（`%m%d`）按基准年补齐、已截止状态把无年份截止约束到过去、`content_required` 由 false 改 true 后 `ok` 行按正文缺失重抓、已截止状态下闰日回看四年、旧新闻行刷新时补写 `title_source=list_title`、已截止状态文本含内部空白（半角/全角空格、NBSP）时仍按过去方向解析、已截止时截止时间须落在列表发布日期与抓取时刻之间（区间无候选保留原文）、公告发布当天截止仍能解析、候选年份覆盖抓取时刻上界（发布日期距抓取超过三年）、配置 `date_xpath` 的项目行把列表发布日期写入 `publish_time`/`publish_time_utc`/`list_publish_time` 且不影响 `deadline`、未配置 `date_xpath` 时 `publish_time` 保持空、增量刷新改写列表发布日期且不抓详情、列表日期本次不可解析时保留旧发布日期、正文重试期间（首轮 `no_content` + 次轮列表日期降级）不抹掉已保留的列表发布日期、日期值不变但来源为历史详情来源时刷新归一为 `list`、列表日期降级沿用非 `list` 旧值时会记 WARNING（未变化的行不重复告警）、没有任何可沿用的本地墙钟时连同来源/原文/`list_publish_time` 一并清空、`publish_time` 为空而 `publish_time_utc` 有值的脏行不会被当成本地墙钟写回、行内 `timezone` 为空时已有 `publish_time_utc` 直接沿用且不写回时区（幂等 skip）、行内既无时区也无 UTC 时按站点配置换算并记 WARNING 且不写回时区（猜出的 UTC 下一轮被沿用、幂等 skip）、站点配置缺 `timezone`（空串）时改用行内时区换算并记 WARNING 且不清空行内已知时区（未变化的行不重复告警、端到端经 `process_entry` 接线仍按行内时区换算） |
| `regression/test_pagination.py` | 40 个用例：模板分页按截止日期停止、模板生成后续页、max_pages 含首页语义、last_url_xpath 多候选取最大页码、跟随 onclick/忽略 # 锚点、渲染后 DOM 找下一页、未知日期不触发 cutoff、已入库页连续早停、旧 list_urls 行为、非法 mode 回退、固定列表忽略 max_pages/分页配置/cutoff、固定页抓取失败不误报、固定列表全空报 empty、置顶旧文不提前停、next XPath 无 href 文本忽略、next XPath 属性 onclick 解析（含 JavaScript: 前缀）、模板缺 {page} 回退、collect_list_entries 包装、分页 URL SSRF 校验（可信内网主机/可信主机 DNS 解析出的内网 IP/公网 IP/未信任私网拒绝）、连接层 IP 字面量保留 scheme+port（`_PinnedHTTPConnection`/`_PinnedHTTPSConnection` 的 `connect` 真实路径）、可信端点 DNS 失败冷却后重试、列表抓取确实收到 validator |

运行方式：`python -X utf8 -m unittest discover -s regression -t . -p "test_*.py" -v`（约 15 秒，无需网络）。

**只在校验“抽取行为本应改变”时才更新基线**：先确认差异是预期的（例如有意调整正文算法），再跑 `python -X utf8 -m regression.generate_baseline`，并在提交信息里说明哪些样本、为什么变。不要为了“让 CI 变绿”而重建基线。

历史背景：重构阶段曾用维护者本机的 `gne_regress.py`（248 用例，含旧提交 `a45b464` 的 A/B 对照，结果 62/62 完全一致），该脚本未被仓库跟踪；其中针对 `tests/` 的核心部分已固化为上面的 `regression/` 套件。

### 提交前必须执行

```bash
docker compose run --rm --entrypoint python crawler -m compileall -q news_crawler.py url_extract.py smoke_fixture.py backfill_raw_content.py smoke_test.py verify_live_news.py api.py api_smoke.py news_api gne regression
docker compose run --rm crawler smoke
docker compose run --rm --entrypoint python crawler -X utf8 api_smoke.py
```

如果本机有 Python 环境（或直接跑 CI 的 `python-checks`），再补这几条——CI 会用同样的命令卡住不通过的 PR：

```bash
ruff check .
mypy
python -X utf8 -m unittest discover -s regression -t . -p "test_*.py" -v
```

离线回归依赖 `tests/` 与 `regression/`，两者都不进镜像（见 `.dockerignore`），所以只能在宿主机运行；容器内 `smoke`/`api_smoke.py` 用的是 `smoke_fixture.py` 的内置样本。

如果改动了抓取或日期逻辑，至少再执行：

```bash
docker compose run --rm crawler test --config sources.local.json --limit 2
```

### 测试缺口（后续可补）

- 目前没有 pytest 单元测试：`regression/` 与 `smoke_test.py` 都用标准库 `unittest`，够用但缺少夹具与参数化；
- 离线回归只比对抽取结果指纹（标题/作者/时间/正文 sha1/图片数/meta 指纹/是否视频页），不校验正文中间过程；
- CI 的 live-news 只覆盖池内 200 个页面（且每次只抽 10/10/10），不覆盖所有站点；站点反爬/改版会导致该 job 波动；
- `content_needs_render()` 的占位判断是启发式规则；
- 日期解析为多正则启发式，缺少系统化用例集。

---

## 11. 已知问题与技术债

按优先级排序：

1. **占位正文判断仍偏启发式**
   `content_needs_render()` 现在规则为：正文为空、或少于 100 字（`MIN_CONTENT_LENGTH`）、或少于 500 字且含加载中关键词。少于 100 字会触发一次浏览器渲染回退；渲染回退后仍少于 100 字时，`extract_article()` 记 WARNING 并照常入库（导航碎片可能因此被当成正文，例如央视的“正在阅读：&lt;标题&gt;”）。渲染回退本身失败时仍返回 `None`（记为 `no_content`，下次重试）。占位词表仍为硬编码，尚未判断占位词出现位置。

2. **正文以单行存储，换行另存 `raw_content`**
   `content` 列仍由 `clean_text()` 归一化为单行（下游检索/展示依赖这一约定，未改动）；段落换行保留在 v4 新增的 `raw_content` 列，只经由 `GET /v1/articles/{id}` 的详情返回（列表即使 `include_content=true` 也为 `null`）。代价是正文体积约翻倍，且历史行（v4 之前入库）`raw_content` 为空，需要 `--force` 重抓，或用 `backfill_raw_content.py` 批量回填（见 §8）。

3. **无法显式强制使用 public 配置**
   `resolve_config_path()` 在存在 `sources.local.json` 时会优先使用它，即使显式传入 `--config sources.json`。建议增加 `--no-local-config` 或显式路径优先级。

4. **日期解析属于多正则启发式**
   不同网站的日期格式可能互相干扰，优先通过站点 `date_xpath` + `date_format` 精确配置；新增格式时必须补充单元测试。

5. **CI 真实抓取覆盖有限**
   `live-news` job 已用 100/50/50 的页面池随机抽样验证静态、JS 渲染与视频三类真实页面（10/10/10 硬门槛），但网络抓取受站点改版/反爬影响大，仍需补充 mock HTML 单测来覆盖解析逻辑。视频类池子现在覆盖 10 个来源站点（新华社、看看新闻、新浪视频、云南广电、澎湃、网易、正北方网、安徽网、中新网、西部网），但除新华社/看看新闻外多数站点只贡献 1 条样本，仍是最薄弱的一环。

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
- `tests/` 是上游资产（新闻页面样本），除非明确需要，不要批量改动；
- `sources.local.json` 是本地文件，不同 Agent 不要互相覆盖，建议各自保存副本。

### 12.4 共享接口约定

- `fetch_html(url, *, url_validator: Callable[[str], None] | None = None) -> str`：失败时抛出异常；请求带 `Accept-Encoding: gzip` 并解压，遇到 br 等无法解码的编码抛 `UnsupportedContentEncoding`；传入 `url_validator` 后每次 HTTP 重定向都会先用新 URL 调用它，抛异常即中断重定向；
- `normalize_images(images, base_url) -> list[str]`：把 `//cdn/x.jpg` 等相对图片地址补全为绝对 URL，去重去空，并只保留 `http/https`（过滤 `data:`/`javascript:`/`ftp:`）；`/v1` 读取侧同样调用，历史行也会被归一化；
- `normalize_images_for_update(images, base_url) -> list[str] | None`：同上，但空结果返回 `None`，供写入侧按"本次未提供"保留旧图，避免把抽取失败误判为明确无图；
- `MAX_DECODED_BYTES = 32 * 1024 * 1024`：解压后响应体上限，超限抛 `UnsupportedContentEncoding`，用于抵御压缩炸弹；
- `close_thread_browser() -> None`：释放当前线程复用的 Playwright 浏览器；`run_source` / `cmd_test` / `extract_article` 的 CLI 入口都会调用；
- `BROWSER_FALLBACK_HTTP_CODES = frozenset({401, 403, 406, 429})`：只有这些状态才回退浏览器，404/410/500 渲染后仍是同样错误；
- `should_skip_stored(conn, source, url, cutoff, timezone_name) -> bool`：已入库条目是否可跳过，`old` 在窗口放宽后返回 False（重抓）；
- `fetch_html_with_browser(url) -> str | None`：失败返回 `None`；
- `content_needs_render(content: str) -> bool`：True 表示需要浏览器渲染（空、短于 100 字、或含加载占位词）；
- `content_is_too_short(content: str) -> bool`：True 表示正文短于 `MIN_CONTENT_LENGTH`（100 字）；
- `ContentExtractor._resolve_body(selector: HtmlElement, body_xpath: str) -> HtmlElement`：按 `body_xpath` 选正文容器；属性/文本表达式（`//div/@id`、`//div/text()`）、XPath 执行异常、未命中、命中空容器都记 WARNING 并回退整页 `//body`，避免配置写错时整站每篇都记 `error`；
- `load_source_xpaths(config_path: str) -> dict[str, str]`（`backfill_raw_content.py`）：读站点配置的 `article_body_xpath`，返回 `{站点名: xpath}`；配置缺失/损坏返回空字典（按 GNE 默认抽取），由 `backfill(..., source_xpaths=...)` 逐条透传；
- `extract_article(url, extractor, *, allow_browser: bool = True, url_validator=None, title_xpath: str = "", publish_time_xpath: str = "", body_xpath: str = "") -> dict | None`：单 URL 完整流程；`allow_browser=False` 时禁用浏览器渲染回退（API 的 extract 默认如此）；`title_xpath`/`publish_time_xpath`/`body_xpath` 透传给 GNE 抽取（crawler 与 API extract 作业共用）；`body_xpath` 未命中时 GNE 回退整页 `//body`；
- `TitleExtractor.extract_with_source(element, title_xpath: str = "") -> tuple[str, str]`：返回 `(title, title_source)`，`title_source` 为 `article_title_xpath`（配置的 XPath 实际命中）或 `gne_default`；
- `TimeExtractor.extract_with_source(element, publish_time_xpath: str = "") -> tuple[str, str]`：返回 `(publish_time, source)`，`source` 为 `xpath`/`labeled_text`/`meta`/`json_ld`/`time_tag`/`text` 等内部来源；
- `publish_source_for(raw: str) -> str`：把 GNE 内部来源映射为落库值 `xpath`/`labeled_text`/`meta`/`text`/`video_meta`/`detail`/`list`，未知值记 `detail`；
- `parse_datetime(value, fmt=None, timezone_name="Asia/Shanghai", *, allow_year_adjust: bool = True, reference_year: int | None = None, allow_future: bool = False) -> dt.datetime | None`：解析中文/数字/相对/ISO 日期；`reference_year` 用于无年份月日按指定年份补齐，避免当前年构造出未来日期；`allow_future=True` 保留晚于当前时间的显式年份（截止时间用），不再回退上一年；
- `collect_list_entries_detailed(source, *, cutoff: dt.datetime | None = None, known_urls: set[str] | None = None) -> dict[str, Any]`：抓取列表页并返回 `items/pages/page_urls/stop_reason`，支持 `pagination` 的 template/next/auto 模式；`collect_list_entries` 保留只返回条目的旧签名；
- `_process_project_entry(conn, source, entry, cutoff, force, delay=0.0) -> str`：项目申报条目入库入口，返回 `ok/old/skip/no_content/error`；`entry` 需含 `title/url/deadline_text/deadline/project_status`；
- `save_article(..., deadline: str = "", deadline_raw: str = "", project_status: str = "", ...) -> str`：新增三个项目申报字段，UPSERT 按空值保留旧值；**deadline 例外**：`deadline_raw` 非空而 `deadline` 为空（本次原文不可解析）时会清空旧 `deadline`，与原文保持一致；
- `parse_deadline_after(raw: str, fmt: str | None, reference: dt.datetime, timezone_name: str = "Asia/Shanghai", *, allow_past: bool = False, not_before: dt.datetime | None = None, not_after: dt.datetime | None = None) -> dt.datetime | None`：解析截止时间；无年份文本按 `reference` 取最近合法年份（`allow_past=True` 时候选年向前 4 年、其余为向后 3 年，覆盖闰日；给了 `not_before`/`not_after` 时扩展到覆盖整个区间年份，避免发布日期距抓取时刻超过三年时选不到上界附近年份）；`allow_past=True`（列表状态为已截止）时取不晚于 `reference` 的最近日期，否则取不早于 `reference` 的最近日期；有年份文本按 `allow_future=True` 保留；`not_before`/`not_after` 限定可信区间（已截止场景传列表发布日期到抓取时刻），区间内无候选返回 `None`（保留原文）；
- `_refresh_project_listing(conn, source_name, url, title, deadline_text, deadline_dt, deadline_raw, project_status, cutoff, timezone_name, content_required, publish_text="", publish_raw="", has_date_xpath=False) -> str | None`：刷新已入库项目行的列表字段（含 `title`/`list_title`）与状态；返回 `skip`/`ok`/`old`，`retry` 表示需要继续抓正文（`content_required=true` 且正文不足：`old` 恢复 `ok` 或原 `ok` 行正文缺失）；刷新时同时把 `title_source` 补写为 `list_title`；`deadline_dt` 为已解析的截止时间（无则 None），用于避免重复解析；`has_date_xpath=True` 时 `publish_text`/`publish_raw` 写入 `publish_time`/`publish_time_raw` 并重算 `publish_time_utc`（本次解析失败则沿用旧值；旧值来源非 `list` 时记 WARNING；没有任何可沿用的本地墙钟时四个字段一并清空），`False` 时清空历史 `publish_time*`（utc 写 NULL）；`publish_time_utc` 的换算口径跟随数据来源：接受本次列表日期时按站点当前 `timezone_name` 换算并把该时区同步写回行内 `timezone`（站点配置缺 `timezone` 时改用行内时区换算并记 WARNING，不会把已知时区清成 NULL；两者都缺时 `publish_time_utc` 留空并记 WARNING），沿用旧墙钟时按行内 `timezone` 换算，避免站点改过时区后把旧墙钟按新时区重算；行内 `timezone` 为空（v6 之前入库的历史行）时，已有 `publish_time_utc` 直接沿用、没有才按站点当前时区换算并记 WARNING，两种情况都不把猜测写回 `timezone`（空串按 NULL 落库）；行内 `timezone` 与目标值不同也会触发一次 UPDATE（补写后稳定，不会每轮刷新）；
- `has_explicit_year(value: Any) -> bool`：日期文本是否自带年份（19xx/20xx 四位或 `18-03-08` 这类两位年份）；用于判断能否安全按当前年或列表日期年份补齐；
- `is_yearless_month_day(value: Any) -> bool`：是否是需要按基准年份补齐的“月日”文本；排除“昨天/前天/N 分钟前/小时前/天前”等相对时间与自带年份的日期；
- `parse_yearless_around(raw: str, reference: dt.datetime, timezone_name: str = "Asia/Shanghai") -> dt.datetime | None`：按基准日期的前一年、当年与下一年分别解析无年份月日，取离基准最近的合法结果（覆盖列表日期在 1 月初/年末两个方向的跨年场景；`parse_datetime(reference_year=当年)` 会因未来日期返回 None）；
- `align_yearless_datetime(value: dt.datetime, reference: dt.datetime, timezone_name: str = "Asia/Shanghai") -> dt.datetime | None`：用 `reference` 前一年、当年与下一年重建无年份日期的月日时分并复算，取离基准最近的合法结果（支持列表日期在 1 月初或年末两个方向的跨年场景）；非法日期（如 2 月 29 日遇上非闰年）返回 `None`；
- `GeneralNewsExtractor.extract(html, **kwargs) -> dict`：正文抽取入口；实例无跨调用可变状态，可跨线程复用（每次 extract 内部新建 ContentExtractor）；
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
