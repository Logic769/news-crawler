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
| `sources.json` | 仓库自带的空站点配置模板 |
| `sources.local.json` | 本地私人站点配置（被 Git 忽略，不会提交） |
| `Dockerfile` | 容器镜像定义（含 Playwright Chromium） |
| `docker-compose.yml` | Docker Compose 编排（挂载配置与数据目录） |
| `docker/entrypoint.sh` | 容器统一入口（run/init/test/smoke/live-news/shell） |
| `docker-deploy.sh` / `docker-deploy.bat` | Docker 一键部署脚本（Linux·macOS / Windows） |
| `smoke_test.py` | 部署后的离线自检脚本 |
| `verify_live_news.py` | CI 实时新闻抓取验证（5 静态 / 3 JS 渲染 / 2 视频） |
| `AGENTS.md` | 多 Agent 协作与开发说明（架构、规范、发布流程） |
| `gne/` | GNE 新闻抽取核心库 |
| `tests/` | 上游自带测试 HTML 样本 |

---

## 环境要求

**Docker 部署只需要：**

- Docker（含 Docker Compose **v2**，Docker Desktop 或 Docker Engine 均可）
- 能访问 Docker Hub / PyPI 的网络

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

```bash
# 默认 5 个静态文本页 + 3 个动态 JS 渲染页 + 2 个视频页
docker compose run --rm crawler live-news

# 可指定数量或固定随机种子
docker compose run --rm crawler live-news --static 5 --dynamic 3 --video 2 --seed 20261030
```

该命令会访问公开新闻站点，用于验证直接 HTTP、Chromium 渲染与视频页三条抽取路径。

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
      "timezone": "Asia/Shanghai"
    }
  ]
}
```

### 配置字段说明

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `name` | 必填 | 站点唯一标识，例如 `ncu_news` |
| `list_url` | 二选一 | 新闻列表页地址 |
| `list_urls` | 二选一 | 多个列表页地址组成的数组，用于分页等情况 |
| `item_xpath` | 必填 | 定位每条新闻节点的 XPath |
| `title_xpath` | 可选 | 标题 XPath，不填默认取第一个 `a` 标签 |
| `url_xpath` | 可选 | 详情页链接 XPath，不填默认取第一个 `a` 标签的 `href` |
| `date_xpath` | 可选 | 发布日期 XPath，不填则从整条节点文本中自动识别 |
| `date_format` | 可选 | 日期格式，例如 `%Y-%m-%d`、`%Y-%m-%d %H:%M` |
| `timezone` | 可选 | 站点时区，默认 `Asia/Shanghai` |
| `render` | 可选 | 设为 `true` 表示列表页需要用 Chrome 渲染后再解析 |
| `delay` | 可选 | 每个请求之间的等待秒数，默认 0.5 |

> 顶层还可以配置 `workers`（同时处理几个站点）和 `days`（默认抓取天数）。

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
| `--workers 5` | 同时处理 5 个站点 |
| `--limit-per-source 20` | 每个站点最多处理 20 条 |
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

- 不要提交任何网站后台地址、API Token、数据库密码或账号信息；
- 抓取公开网站前，请阅读目标网站的 `robots.txt`、服务条款和访问频率要求；
- 请合理设置 `delay`，避免对目标网站造成压力；
- 站点配置、数据库和挂载目录都属于本地数据，不要误提交到 Git。

---

## 相关链接

- [本项目仓库](https://github.com/Logic769/news-crawler)
- [原项目 GeneralNewsExtractor](https://github.com/kingname/GeneralNewsExtractor)
