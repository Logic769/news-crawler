# 新闻爬虫（News Crawler）

一个基于 [GNE（GeneralNewsExtractor）](https://github.com/kingname/GeneralNewsExtractor) 的多站点新闻抓取工具。

它可以把网页新闻的**标题、发布时间、作者、正文、图片**自动提取出来，并按时间范围筛选，适合需要定时收集多个网站新闻、通知或公告的场景。

---

## 它能做什么

- 输入一篇新闻文章网址，直接输出标题、发布时间、作者、正文、图片；
- 配置多个网站列表页，自动抓取“最近 N 天”的新闻；
- 自动识别大多数常见日期格式；
- 页面正文由 JavaScript 加载时，自动使用 Chrome/Chromium 渲染后再提取；
- 抓取结果自动存入 SQLite，避免重复抓取；
- 使用 Docker 一键部署，并提供部署后的冒烟测试与实时新闻抓取验证。

> 注意：GNE 本身是“正文抽取器”，不是通用爬虫框架。本工具负责请求网页，GNE 负责从 HTML 中提取新闻正文。

---

## 项目结构

| 文件 / 目录 | 作用 |
| --- | --- |
| `news_crawler.py` | 多站点批量抓取主程序 |
| `url_extract.py` | 单条文章网址抽取工具 |
| `api.py` | FastAPI 应用入口（`uvicorn api:app`） |
| `news_api/` | 新闻查询 API、异步任务与 SQLite 读模型 |
| `api_smoke.py` | API 离线冒烟测试（CI 使用） |
| `sources.json` | 仓库自带的空站点配置模板 |
| `sources.local.json` | 本地私人站点配置（被 Git 忽略，不会提交） |
| `Dockerfile` | 容器镜像定义（含 Playwright Chromium） |
| `docker-compose.yml` | Docker Compose 编排（挂载配置与数据目录） |
| `docker/entrypoint.sh` | 容器统一入口（run/init/test/api/smoke/live-news/shell） |
| `docker-deploy.sh` / `docker-deploy.bat` | Docker 一键部署脚本（Linux·macOS / Windows） |
| `smoke_test.py` | 部署后的离线自检脚本 |
| `verify_live_news.py` | 实时新闻抓取验证（默认抽取 10 静态 / 10 JS 渲染 / 10 视频） |
| `live_news_pool.json` | 实时验证页面池（100 静态 / 50 JS 渲染 / 50 视频） |
| `AGENTS.md` | 多 Agent 协作与开发说明（架构、规范、发布流程） |
| `gne/` | 新闻抽取核心库（重构版，按字段拆分于 `gne/extractors/`） |
| `tests/` | 上游自带测试 HTML 样本 |

---

## 环境要求

**Docker 部署只需要：**

- Docker（含 Docker Compose **v2**，Docker Desktop 或 Docker Engine 均可）
- 能访问 Docker Hub / PyPI 的网络

**本地 Python 环境（可选，主要用于开发）：**

- Python 3.10+，`pip install -e ".[browser,api]"`；
- ⚠️ 本项目发行包名同样是 `gne`（与 PyPI 上游 GeneralNewsExtractor 相同）。本仓库不向 PyPI 发布，但 `pip install -e .` 生成的 `gne-1.1.2.dist-info` 与已安装的上游 `gne` 在同一环境会互相覆盖，请使用独立虚拟环境，不要与 PyPI 的 `gne` 混装。

---

## 使用 Docker 部署

先在项目根目录（首次使用可执行 `git clone https://github.com/Logic769/news-crawler.git && cd news-crawler`）操作。

Docker 方式把运行环境（Python、依赖、Playwright Chromium）全部封装进镜像，一次构建即可在 Windows / Linux / macOS 上一致运行；更新时只需重新构建镜像，数据与配置通过挂载保留，便于统一管理和跨平台更新。

### 一键部署

Linux / macOS：

```bash
chmod +x docker-deploy.sh
./docker-deploy.sh
```

Windows（PowerShell 或直接双击）：

```powershell
.\docker-deploy.bat
```

脚本会自动完成：构建镜像 → 初始化数据库 → 运行冒烟测试，看到 `SMOKE TEST PASSED` 即表示成功。

### 手动步骤（等价）

```bash
# 1. 准备私有站点配置（首次）
cp sources.json sources.local.json   # Windows 用：Copy-Item sources.json sources.local.json
                                     # 然后按“多站点批量抓取”章节填写站点

# 2. 构建镜像
docker compose build

# 3. 初始化数据库 + 冒烟测试
docker compose run --rm crawler init
docker compose run --rm crawler smoke
```

### 运行抓取

```bash
docker compose run --rm crawler run --config sources.local.json --days 3
```

常用参数：`--days`、`--since`、`--workers`、`--limit-per-source`、`--force`。
测试列表解析：

```bash
docker compose run --rm crawler test --config sources.local.json --name <站点名>
```

### 实时新闻抓取验证

仓库内置 `live_news_pool.json`（100 个静态文本页 / 50 个 JS 渲染页 / 50 个视频页，覆盖国内媒体、高校与政府网站），从中随机抽取页面做真实抽取：

```bash
# 默认抽取 10 静态 + 10 JS 渲染 + 10 视频，全部通过才算成功
docker compose run --rm crawler live-news

# 指定数量或固定随机种子（便于复现失败）
docker compose run --rm crawler live-news --static 10 --dynamic 10 --video 10 --seed 20261030
```

该命令访问公开新闻站点，覆盖直接 HTTP、Chromium 渲染与视频页三条抽取路径。抽到的页面失效时会自动从池中补抽（日志打印 `REPLACED PAGES`），补不足目标数量则直接失败；失败时按日志里的 `LIVE NEWS SEED` 用 `--seed` 复现。池子维护方法见 `AGENTS.md` 第 10 章。

### 数据与配置

- 私有站点配置 `sources.local.json` 以**只读**方式挂载进容器，不会打包进镜像，也不会提交到 Git；
- 数据库落在宿主机 `./data/news.db`（容器内 `/app/data/news.db`），重新构建镜像不会丢失；
- 已有的旧 `news.db` 可直接移动到 `data/news.db` 继续使用；
- 首次使用裸 `docker compose run` 前请确保已存在 `sources.local.json`（否则 Docker 会把只读挂载点建成目录导致报错）；`docker-deploy.sh`/`.bat` 已自动处理；
- 容器默认以 root 运行，以规避 Windows/Linux 之间 bind mount 的 UID 权限不一致；如需更高安全可改用非 root 用户 + 命名卷；
- **默认镜像自带浏览器**：默认构建即安装 Playwright Chromium（`INSTALL_BROWSER=1`），可处理 JS 动态页面与视频页；仅在确定不需要浏览器时用 `INSTALL_BROWSER=0` 构建精简版。
- **视频页适配**：对 `/shipin/`、`/video/` 等视频页，改用页面 meta、JSON-LD 与 URL 日期提取标题、简介与发布时间，避免正文过短和发布时间误判（默认镜像已带 Chromium，可渲染后再判断）。
- **构建可复现**：基础镜像按 digest 固定（`Dockerfile` 的 `FROM ...@sha256:...`），依赖版本由 `docker/constraints.txt` 锁定（源自 `uv.lock`）；升级依赖请同步 `uv.lock` 与 `docker/constraints.txt`。

### 国内网络加速（可选）

```bash
# PyPI 镜像加速
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple docker compose build

# 同时加速 Playwright 浏览器下载
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
docker compose build
```

> 不需要 JS 渲染、希望镜像更小：`INSTALL_BROWSER=0 docker compose build`（之后遇到动态页面将无法回退渲染）。

### 更新项目（Docker）

```bash
git pull
docker compose build                      # 重新构建镜像
docker compose run --rm crawler smoke     # 可选：验证
```

数据与配置都在宿主机挂载目录中，更新镜像不影响已有数据。

---

## 新闻 API（/v1）

抓取结果除了直接读 SQLite，还可以通过标准 HTTP API 提供给下游系统。API 与抓取共用同一个数据库，默认只读查询；抓取动作通过异步任务执行。

### 启动 API

```bash
docker compose up -d api
docker compose logs -f api
curl http://localhost:8000/v1/health
```

- Swagger 文档：<http://localhost:8000/docs>
- OpenAPI：<http://localhost:8000/openapi.json>
- 容器内固定监听 `8000`；宿主机端口用 `NEWS_API_PORT` 覆盖（例如 `NEWS_API_PORT=9000 docker compose up -d api`）。
- 只启动 API 不会自动抓取；抓取仍用 `crawler` 服务或下面的异步任务。

### 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NEWS_DB` | `/app/data/news.db` | SQLite 路径（compose 已设置） |
| `NEWS_CONFIG` | 代码内 `sources.json`；compose 设为 `/app/sources.local.json` | 站点配置路径 |
| `NEWS_API_KEY` | 空 | 设置后所有 `/v1` 业务接口校验 API Key；`/v1/health` 与文档保持开放 |
| `NEWS_API_BIND` | `127.0.0.1` | 仅 compose 的宿主机监听地址；需要局域网访问时设为 `0.0.0.0` |
| `NEWS_API_PORT` | `8000` | 仅 compose 的宿主机映射端口 |
| `NEWS_JOB_WORKERS` | `1` | 异步任务并发数；SQLite 单写者，建议保持 1 |
| `NEWS_TIMEZONE` | `Asia/Shanghai` | 默认时区，用于无时区时间与日期过滤 |
| `NEWS_CORS_ORIGINS` | 空 | 逗号分隔的允许来源；为空不启用 CORS |
| `NEWS_ALLOW_PRIVATE_URLS` | 空（关闭） | 允许 `extract` 抓取回环/内网地址；仅可信环境开启 |
| `NEWS_EXTRACT_ALLOW_BROWSER` | 空（关闭） | 允许 `extract` 在直接抓取失败时回退无头浏览器；仅在可信环境开启 |
| `NEWS_MAX_PENDING_JOBS` | `50` | 排队 + 运行中的作业上限，超出返回 `429`；相同类型与参数的未完成作业会复用 |
| `NEWS_REQUIRE_AUTH` | 空（关闭） | 设为 `1` 时要求必须配置 `NEWS_API_KEY`，否则业务接口一律返回 `401` |
| `NEWS_INSECURE_SSL` | 空（关闭） | 设为 `1`/`true`/`yes`/`on` 时，证书校验失败会打印 WARNING 并降级为不校验重试；默认直接抛错，避免中间人攻击 |

> 安全提示：未设置 `NEWS_API_KEY` 时接口完全开放。compose 默认只监听 `127.0.0.1`；如需局域网访问，请同时设置强随机 `NEWS_API_KEY` 再改 `NEWS_API_BIND=0.0.0.0`。`extract` 默认拒绝回环、内网与保留地址，并在每次 HTTP 重定向时重新校验目标；默认还禁用浏览器回退，避免通过页面 JS 绕过地址限制。确需抓取内网新闻时设置 `NEWS_ALLOW_PRIVATE_URLS=1`；确需浏览器渲染时再加 `NEWS_EXTRACT_ALLOW_BROWSER=1`。
>
> 开启 `NEWS_EXTRACT_ALLOW_BROWSER=1` 等于放弃上述地址限制：Chromium 会自行解析 DNS 并跟随跳转，`extract` 只对初始 URL 与 HTTP 层的每一跳做校验。另外 DNS 解析与真正建连是两次独立查询，仍存在 DNS rebinding 的理论窗口。因此公网部署请始终设置 `NEWS_API_KEY`（或 `NEWS_REQUIRE_AUTH=1`），并保持浏览器回退关闭。

> 用 `docker compose` 部署时，API 的变量通过 compose 的白名单透传，需要在项目根目录的 `.env` 或当前 shell 中设置后才生效（例如 `NEWS_API_KEY=... docker compose up -d api`）。

设置 API Key 的示例（bash）：

```bash
NEWS_API_KEY=change-me docker compose up -d api
curl -H "X-API-Key: change-me" http://localhost:8000/v1/articles
# 也支持：-H "Authorization: Bearer change-me"
```

### 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/health` | 健康检查，不需要 API Key |
| GET | `/v1/articles` | 分页查询文章列表 |
| GET | `/v1/articles/{id}` | 文章详情（始终包含正文） |
| GET | `/v1/sources` | 按站点聚合的统计 |
| GET | `/v1/stats` | 全库统计 |
| POST | `/v1/jobs` | 创建异步任务（`crawl` 或 `extract`） |
| GET | `/v1/jobs` | 任务列表 |
| GET | `/v1/jobs/{id}` | 任务状态与结果 |

`/v1/health` 在数据库异常时返回 `503` 和 `{"error":{"code":"database_unavailable","status":503,"message":"数据库不可用"}}`，可直接作为容器健康检查探针。其余错误统一为 `{"error":{"code":"http_error","status":<HTTP 状态码>,"message":"..."}}`；参数校验失败时 `code` 为 `validation_error`、`status` 为 `422`，并额外带 `details`。

### 查询文章

```bash
# 最近发布（默认只返回 status=ok，列表不含正文）
curl "http://localhost:8000/v1/articles?page_size=20"

# 需要正文时显式开启
curl "http://localhost:8000/v1/articles?include_content=true&page_size=5"

# 按站点、时间范围、关键词过滤
curl "http://localhost:8000/v1/articles?source=chinanews&published_from=2026-10-01&published_to=2026-10-30&q=经济"

# 查看全部状态（ok/old/no_content/unknown_date/error）
curl "http://localhost:8000/v1/articles?status=all&page_size=50"
```

| 参数 | 说明 |
| --- | --- |
| `status` | `ok`（默认）、`old`、`no_content`、`unknown_date`、`error`、`all` |
| `source` | 站点名，对应配置里的 `name` |
| `published_from` / `published_to` | 日期或 ISO 时间；只写日期时按 `NEWS_TIMEZONE` 解释，结束日期包含当天。项目申报行 `publish_time` 为空，会被这两个参数排除 |
| `deadline_from` / `deadline_to` | 项目申报专用：按 `deadline` 过滤（站点墙钟），只写日期时结束日期包含当天；带时区偏移的时间只取墙钟部分，不做时区换算；与 `published_from`/`published_to` 同时传会 AND 成空集，请二选一 |
| `q` | 标题、列表标题、正文的模糊搜索 |
| `page` / `page_size` | 分页，`page_size` 最大 100 |
| `order` | `published_desc`（默认）、`published_asc`、`updated_desc`、`deadline_desc`、`deadline_asc` |
| `include_content` | 列表是否返回正文，默认 `false` |

文章对象中的关键字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定文章 ID（按 `source + url` 生成），详情接口使用 |
| `title` / `author` | 标题、作者 |
| `published_at` | 带时区的 ISO 8601 发布时间，例如 `2026-10-30T10:00:00+08:00` |
| `published_at_utc` | 同一时间的 UTC 表示，以 `Z` 结尾 |
| `published_raw` | 数据库里的原始发布时间字符串 |
| `publish_time_source` | 发布时间来源：`xpath`（详情页配置的 XPath）、`labeled_text`（“发布时间：…”标签）、`meta`（meta/JSON-LD/time 标签）、`text`（正文扫描）、`video_meta`（视频页 meta）、`detail`（其他详情页来源）、`list`（回退列表页日期）；历史行或无法判定时可能为空字符串 |
| `publish_time_raw` | 详情页抽取到的发布时间文本（XPath 场景可能是匹配出的日期子串；文本扫描可能只有“09月30日”这类无年份日期），便于核对日期为什么被回退 |
| `list_publish_time` | 列表页解析出的日期；详情日期缺失或与列表相差过大时用于交叉校验 |
| `title_source` | 标题来源：`article_title_xpath`（配置的标题 XPath **实际命中**）、`gne_default`（未命中时回退 GNE 自动识别）或 `list_title`（项目申报列表行使用列表标题） |
| `deadline` / `deadline_raw` | 项目申报专用：解析后的截止时间（`YYYY-MM-DD HH:MM:SS`）与列表页原文；新闻站点为空 |
| `project_status` | 项目申报专用：列表页的申报状态文本（如“申报中”“已截止”）；新闻站点为空 |
| `timezone` | 该文章写入时使用的站点时区（落库保存，站点配置变化不影响历史数据；`unknown_date` 等没有发布时间的结果不会写入该列，API extract 路径同样如此） |
| `content` / `content_length` | 正文与字数（已归一化为单行；列表默认不返回，详情始终返回） |
| `raw_content` | GNE 原始正文，保留段落换行；只在 `GET /v1/articles/{id}` 详情返回，列表（含 `include_content=true`）为 `null`；v4 之前入库的历史行为空，需 `--force` 重抓，或用 `backfill_raw_content.py` 回填（先 `--dry-run` 看候选数，再 `--limit 50` 小批试跑；容器内必须显式传 `--db /app/data/news.db`，命令见 AGENTS.md §8） |
| `images` | 图片 URL 数组 |
| `status` / `error` | 抓取状态与错误信息 |

> 时间排序与过滤基于数据库内的站点本地时间字符串。若同时接入不同时区的来源，分页排序与 `published_from`/`published_to` 区间可能存在偏差：建议统一各站点的 `timezone`，或在展示层用 `published_at_utc` 做二次校准。升级前写入的老数据没有 `timezone` 列，会回落到当前配置推断。
>
> 详情页日期若晚于当前时间超过 1 天，视为不可信：不会再自动减一年，而是回退到列表页日期；列表日期也缺失时记 `unknown_date`。
>
> GNE 的文本扫描可能只抽到 `09月30日` 这类无年份日期：主抓取路径会优先用列表日期的年份补齐；列表日期也缺失时，以及 API 单 URL extract 没有列表日期可交叉校验时，这类日期会记 `unknown_date`，不会按当前年猜测。
>
> `old` 分支的 `publish_time_source` 取决于判定时机：列表阶段就确认超出窗口时记 `list`；详情抓取后才发现超出窗口时保留详情来源（`xpath`/`labeled_text`/`meta`/`text`）。`publish_time_raw` 与当前 `publish_time` 同步：回退到列表日期时清空，不再保留旧详情原文；`unknown_date` 本次没有可信发布时间，新插入行会保留本次抽取文本便于排查，已有行则保留旧值与旧时间配对，避免“旧时间挂新来源”；新行的 `publish_time_source` 为空字符串，已有行保留旧来源。

详情示例：

```bash
curl "http://localhost:8000/v1/articles/<article_id>"
```

### 异步抓取

创建任务后立即返回 `202` 和任务 ID，用任务详情轮询结果。任务持久化在 SQLite 的 `crawl_jobs` 表，服务重启时 `queued` 任务会继续执行，中断的 `running` 任务会标记为失败。排队与运行中的作业总数受 `NEWS_MAX_PENDING_JOBS` 限制，超出返回 `429`；相同类型与参数的未完成作业会复用同一任务 ID；已完成作业保留 30 天后自动清理。
>
> 任务写库采用“部分更新”语义：某次抓取失败只会把该文章标记为 `error` 并记录原因，已经成功抓到的正文、标题、发布时间不会被清空。

```bash
# 按 sources.local.json 抓取最近 3 天
curl -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"type":"crawl","days":3}'

# 只抓某个站点，并限制条数
curl -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"type":"crawl","source":"chinanews","days":1,"limit_per_source":5}'

# 抓取单篇文章（默认 source=on_demand）
curl -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"type":"extract","url":"https://example.com/article.html"}'

# 轮询
curl http://localhost:8000/v1/jobs/<job_id>
```

`crawl` 参数：`source`、`days`、`since`（`YYYY-MM-DD`）、`workers`、`limit_per_source`、`force`。
`extract` 参数：`url`（必填）、`source`。

`extract` 的目标地址在提交时即校验：回环、内网与保留地址返回 `400`（除非设置 `NEWS_ALLOW_PRIVATE_URLS=1`）。抓取或抽取失败时作业状态为 `failed`，同时该 URL 在 `articles` 中留一条 `status=error` 记录，便于后续重试。

> 查询与抓取可以同时进行；但手动 `docker compose run crawler run` 与 `crawl` 任务都会写同一个 SQLite，建议错开执行，或把 `NEWS_JOB_WORKERS` 保持为 1。

---

## 快速体验：抓取单篇文章

```bash
docker compose run --rm --entrypoint python crawler -X utf8 url_extract.py "https://example.com/article.html"
```

把网址替换成真实新闻文章页。成功后会在终端输出 JSON：

```json
{
  "url": "https://example.com/article.html",
  "title": "新闻标题",
  "publish_time": "2026-09-07 18:00:00",
  "author": "作者",
  "content": "新闻正文……",
  "images": []
}
```

---

## 多站点批量抓取

### 第 1 步：创建本地站点配置

推荐使用 `sources.local.json` 保存自己的站点配置，这样即使以后提交代码，也不会把站点列表公开：

Windows：

```powershell
Copy-Item sources.json sources.local.json
```

Linux / macOS：

```bash
cp sources.json sources.local.json
```

### 第 2 步：填写站点配置

用任意文本编辑器打开 `sources.local.json`，在 `sources` 数组中添加站点：

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
      "date_skew_days": 7
    }
  ]
}
```

### 配置字段说明

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `name` | 必填 | 站点唯一标识，例如 `ncu_news` |
| `list_url` | 二选一 | 新闻列表页地址 |
| `list_urls` | 二选一 | 多个固定列表页地址组成的数组（旧版分页方式；单 `list_url` 站点请优先用 `pagination`） |
| `item_xpath` | 必填 | 定位每条新闻节点的 XPath |
| `title_xpath` | 可选 | 标题 XPath，不填默认取第一个 `a` 标签 |
| `url_xpath` | 可选 | 详情页链接 XPath，不填默认取第一个 `a` 标签的 `href` |
| `date_xpath` | 可选 | 发布日期 XPath，不填则从整条节点文本中自动识别 |
| `date_format` | 可选 | 日期格式，例如 `%Y-%m-%d`、`%Y-%m-%d %H:%M` |
| `timezone` | 可选 | 站点时区，默认 `Asia/Shanghai` |
| `article_title_xpath` | 可选 | 详情页标题 XPath。用于 `<title>` 写的是站点名、真标题在正文 `h1` 的站点。推荐直接写标题元素（如 `//h1[@class='article-title']`），会取其全部文本（含 `<em>` 等子元素）；写成 `//h1/text()` 只能取第一个直接文本节点，标题含子标签时会截断。以 `string(` 开头的表达式会被忽略 |
| `article_publish_time_xpath` | 可选 | 详情页发布时间 XPath。用于正文里更早出现无关时间、页头日期才是真发布时间的站点；推荐写包含日期的元素（如 `//p[contains(@class,'article__subTitle')]/span[1]`）。以 `string(` 开头或非法表达式会被忽略 |
| `date_skew_days` | 可选 | 详情日期与列表日期允许相差的天数，默认 7，范围 0–365；超过时改用列表日期，避免正文里的未来时间或站内推荐日期污染发布时间 |
| `listing_type` | 可选 | 列表类型：`news`（默认）或 `project_application`。项目申报模式把列表本身当作数据源，见下方“项目申报列表” |
| `deadline_xpath` | 可选 | 项目申报：截止时间所在单元格 XPath（相对条目节点） |
| `deadline_format` | 可选 | 项目申报：截止时间格式，默认回退 `date_format` |
| `project_status_xpath` | 可选 | 项目申报：申报状态所在单元格 XPath |
| `content_required` | 可选 | 项目申报：是否必须抓到详情正文。默认 `false`（登录墙/无正文仍记 `ok` 并存列表字段）；设为 `true` 退回新闻语义（无正文记 `no_content`/`error`） |
| `pagination` | 可选 | 自动分页配置对象，见下方“自动分页（可选）”；只对单个 `list_url` 生效，配置 `list_urls` 时仍按固定地址列表抓取（不受 `max_pages`/`stop_when_older_than_cutoff` 影响） |
| `render` | 可选 | 设为 `true` 表示列表页需要用 Chrome 渲染后再解析 |
| `delay` | 可选 | 每个请求之间的等待秒数，默认 0.3 |

> 顶层还可以配置 `workers`（同时处理几个站点，默认 16）和 `days`（默认抓取天数）。
>
> **内存提示**：`workers` 是站点级线程数；开启浏览器回退时，每个线程可能各自启动一个 Chromium（约 200–300 MB）。`workers=16` 建议至少 4 GB 可用内存；小内存机器或 JS 站点较多时请调低 `--workers`，否则可能被 OOM Kill。

#### 自动分页（可选）

列表页是 `index_2.html` 这类固定模板时，不必再手写 `list_urls`：

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
| `last_url_xpath` | 空 | 尾页链接 XPath；命中元素时读取 href/onclick/data-page/value 和文本作为候选，最终取最大页码（`data-page="12"` 这类纯数字直接作为页码）。不填时从整页 HTML 用 `total_pages_regex` 探测 |
| `next_url_xpath` | 空 | “下一页”链接 XPath；支持相对地址，也兼容常见 `onclick=location.href=...` 写法 |
| `total_pages_regex` | `(?:index_\|page[=/])(\\d+)` | 从 HTML 提取最大页码的正则，捕获组必须是数字（表格中 `\\|` 表示正则里的或） |
| `start` | `1` | 模板模式从第几页开始生成（第 1 页始终是 `list_url`，通常设为 2）；范围 1–1000 |
| `max_pages` | `50` | 最多抓取页数（含 `list_url` 计 1 页，后续页最多 `max_pages-1` 个）；范围 1–500 |
| `stop_when_older_than_cutoff` | `true` | 当前页最晚日期早于抓取窗口起点时停止翻页（整页每条都能解析出日期且都早于窗口才停，避免置顶旧文或缺失日期导致漏抓）；项目申报模式不按发布日期早停，窗口判断只看 `deadline` |
| `stop_after_known_pages` | `0` | 连续 N 页链接都已入库时停止（增量运行）；范围 0–100，默认 0 关闭；仅 template/next/auto 模式生效。开启后若上次运行更后页失败，可能提前停止，建议确认完整抓取后再开 |

未配置 `render: true` 时，列表页解析为空会自动用 Chrome 渲染一次，并基于渲染后的 DOM 重新解析与继续翻页。自动分页会在页内按 URL 去重，并在 `test` 命令输出页数与停止原因（如 `cutoff`、`known_pages`、`no_next`、`max_pages`、`empty`、`fetch_failed`、`done`）。

自动分页会为列表页启用 SSRF 地址白名单：配置的 `list_url`/`list_urls` 是唯一可信目标，且要求 scheme + host + port 完全一致；其他目标必须是公网地址，并且只允许 http 80 / https 443 端口。指向配置主机其他端口的链接会被拒绝；同端口无法按路径区分，配置主机本身视为可信。带地址校验的列表页请求为直连（不读取 `HTTP(S)_PROXY`），文章详情页保持原有代理行为。浏览器回退时每个请求都会重新校验，但 Playwright 自行解析 DNS，无法像 HTTP 路径那样在连接层钉扎 IP，残余 DNS rebinding 风险需由出口网络策略兜底。

#### 项目申报列表（`listing_type: project_application`）

项目申报公告的列表页本身就是完整数据：标题、截止时间、申报状态和详情链接都在表格里，而详情页常需要登录。把站点声明为项目申报类型后：

- `deadline_xpath` 解析出的截止时间写入 `deadline`/`deadline_raw`，**不会**被当成 `publish_time`（项目行 `published_at` 为空，按发布时间排序时会排在末尾）；`/v1/articles` 提供 `order=deadline_desc|deadline_asc` 与 `deadline_from`/`deadline_to` 专门查询项目公告，注意 `published_from`/`published_to` 会把项目行全部排除；
- `project_status_xpath` 解析出的状态写入 `project_status`；
- `title` 与 `list_title` 都写列表标题，`title_source=list_title`；
- `content_required: false`（默认）时，详情页抓不到正文、或正文短于 100 字（登录墙占位）不会记为失败，只保存列表字段，下次运行也不会重复重试；需要正文的站点请显式设为 `true`；
- 截止时间早于抓取窗口起点（`--days`/`--since`）的条目记为 `old`，避免每次重跑都处理历年公告；`--force` 可强制重抓；
- 已入库的 `ok`/`old` 行会在后续运行中按列表字段刷新 `title`/`list_title`/`title_source`/`deadline`/`deadline_raw`/`project_status`（延期、标题修订、截止状态变化都无需重抓详情）；原 `old` 行若截止时间原文非空且变得不可解析（如“常年受理”）会恢复为 `ok`，`content_required: true` 时继续抓正文（列表截止时间单元格为空时保留旧 `deadline` 与 `old` 状态，不会恢复）；`content_required` 由 `false` 改为 `true` 后，正文缺失的 `ok` 行也会自动补抓；
- 同一 URL 从新闻模式切换为项目模式时，历史 `publish_time`/`publish_time_source`/`publish_time_raw`/`list_publish_time` 会被清空，但 `content`/`raw_content`/`author`/`images` 保留（可能残留旧新闻正文，查询侧请以 `title_source=list_title` 或站点配置识别项目行，不要只按 `deadline`；`deadline_from`/`deadline_to` 只对有可解析截止时间的项目行生效；项目切回新闻模式并重新抓取后旧 `deadline`/`deadline_raw`/`project_status` 仍会保留，这类新闻行也可能命中截止时间过滤、或被按 `project_status` 误判为项目行，查询侧请按站点/字段区分行类型）
- 无年份的截止时间（如“11月30日”）按列表发布日期（缺失时按当前时间）取最近的合法年份：列表状态为已截止时取不晚于该日期、且落在列表发布日期与抓取时刻之间的最近年份（列表发布日期缺失时只约束上界；区间内无候选时 `deadline` 留空、仅保留原文），否则取不早于该日期的最近年份，不会被减一年；配置了无年份 `deadline_format`（如 `%m月%d日`）时同样按基准年补齐；注意重抓历年已截止公告时，无年份截止时间可能随抓取年份后移（“取最近合法年份”的已知取舍，已有 `deadline` 的行可用 `--force` 之外的常规运行保留原值）。

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

### 第 3 步：测试列表解析

先只测试一个站点，确认标题、链接、日期能正确解析：

```bash
docker compose run --rm crawler test --config sources.local.json --name 你的站点标识
```

如果输出为空、标题重复或日期不对，需要调整该站点的 XPath。

### 第 4 步：运行批量抓取

```bash
docker compose run --rm crawler run --config sources.local.json --days 3
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--days 3` | 抓取最近 3 天 |
| `--since 2026-09-01` | 从指定日期开始抓取 |
| `--workers 16` | 同时处理 16 个站点 |
| `--limit-per-source 20` | 每个站点最多处理 20 条（只计实际处理的条目，已入库被跳过的不占名额） |
| `--force` | 忽略数据库已有记录，强制重新抓取 |

---

## 查看抓取结果

结果默认保存在项目目录的 `data/news.db` 中。

查看最近抓取的文章：

```sql
SELECT source, title, publish_time, url
FROM articles
WHERE status = 'ok'
ORDER BY publish_time DESC;
```

只查看最近 3 天：

```sql
SELECT source, title, publish_time, url
FROM articles
WHERE status = 'ok'
  AND datetime(publish_time) >= datetime('now', '-3 days', 'localtime')
ORDER BY publish_time DESC;
```

也可以用图形工具 [DB Browser for SQLite](https://sqlitebrowser.org/) 打开 `data/news.db`。

### 数据状态说明

| 状态 | 含义 |
| --- | --- |
| `ok` | 成功抓取且发布时间在时间范围内 |
| `old` | 已确认超出时间范围，不会重复抓取 |
| `skip` | 数据库已有记录，本次跳过 |
| `no_content` | 正文提取失败 |
| `unknown_date` | 无法解析发布日期 |
| `error` | 抓取过程出错 |
| `list_empty` | 列表页没有解析到条目 |

---

## 工作原理

1. 读取配置文件中的站点列表；
2. 抓取新闻列表页 HTML；
3. 按 XPath 解析出标题、链接、发布日期；
4. 过滤出最近 N 天的文章；
5. 逐篇抓取详情页；
6. 用 GNE 提取标题和正文；必要时使用 Chrome 渲染；
7. 结果写入 SQLite，按 `(source, url)` 去重。

---

## 常见问题

### 1. 提示找不到 docker

请先安装 Docker Desktop（Windows/macOS）或 Docker Engine + Compose v2（Linux），并确保重新打开终端后 `docker --version` 能正常输出版本。

### 2. 提示 docker compose 不是命令 / 版本过低

本项目使用 Compose v2（`docker compose`）。请升级 Docker Desktop 或安装 Compose v2 插件；旧的 `docker-compose`（v1）不再推荐。

### 3. 抓取后正文为空，或者只有“正在加载”

说明目标页面正文由 JavaScript 动态加载。默认镜像已自带 Playwright Chromium，工具会自动回退渲染；如果仍失败，请检查容器内浏览器是否可正常启动（`docker compose run --rm crawler smoke`）。

### 4. 站点配置里的真实网址会被上传吗

不会。只要把站点配置放在 `sources.local.json`，该文件已被 `.gitignore` 忽略。

### 5. 一个站点抓取出错会影响其他站点吗

不会。单个文章异常会被记录为 `error`，其他文章和站点会继续抓取。

### 6. 如何更新项目

Docker 方式：

```bash
git pull
docker compose build
```

---

## 安全与合规建议

- 抓取公开网站前，请阅读目标网站的 `robots.txt`、服务条款和访问频率要求；
- 请合理设置 `delay`，避免对目标网站造成压力。

---

## 许可证

本项目沿袭上游 [GeneralNewsExtractor](https://github.com/kingname/GeneralNewsExtractor) 的授权方式，以 **GNU GPL v3（GPL-3.0-only）** 发布，完整文本见 [LICENSE](LICENSE)。

按 GPLv3 的要求，向他人传递本项目或其修改版的副本时，必须遵守 GPLv3 的相应条件：传递修改后的源代码时，整个作品需按 GPLv3 授权；传递目标代码时，需按 GPLv3 第 6 节规定的方式（随附源代码、书面要约或同等网络访问等）提供对应的源代码。仅在本地运行或修改、或仅通过网络提供服务而不向他人传递副本，不触发上述义务。如需在 GPLv3 之外以其它许可证分发本项目或其修改版，请联系相关版权所有者取得另行授权。

---

## 相关链接

- [本项目仓库](https://github.com/Logic769/news-crawler)
- [原项目 GeneralNewsExtractor](https://github.com/kingname/GeneralNewsExtractor)
