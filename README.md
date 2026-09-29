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
- 提供 Windows、Linux/macOS 一键部署脚本和冒烟测试。

> 注意：GNE 本身是“正文抽取器”，不是通用爬虫框架。本工具负责请求网页，GNE 负责从 HTML 中提取新闻正文。

---

## 项目结构

| 文件 / 目录 | 作用 |
| --- | --- |
| `news_crawler.py` | 多站点批量抓取主程序 |
| `url_extract.py` | 单条文章网址抽取工具 |
| `sources.json` | 仓库自带的空站点配置模板 |
| `sources.local.json` | 本地私人站点配置（被 Git 忽略，不会提交） |
| `deploy.bat` / `deploy.ps1` | Windows 一键部署脚本 |
| `deploy.sh` | Linux/macOS 一键部署脚本 |
| `smoke_test.py` | 部署后的离线自检脚本 |
| `AGENTS.md` | 多 Agent 协作与开发说明（架构、规范、发布流程） |
| `gne/` | GNE 新闻抽取核心库 |
| `tests/` | 上游自带测试 HTML 样本 |

---

## 环境要求

- Python 3.10 或更高版本
- Git
- 能访问 GitHub / PyPI 的网络
- 可选：Chrome 或 Chromium（用于 JS 渲染；没有时部署脚本会自动安装 Playwright Chromium）

---

## 国内网络加速（可选）

如果直接访问 GitHub 或 PyPI 速度很慢，可以先按下面的方式加速。网络正常时不需要这些步骤。

### GitHub 克隆加速

GitHub 克隆/下载慢时，可以使用第三方代理镜像（仅用于网络加速）：

```bash
git clone https://ghfast.top/https://github.com/Logic769/news-crawler.git
```

如果某个镜像失效，可以尝试：

```bash
git clone https://gh-proxy.com/https://github.com/Logic769/news-crawler.git
git clone https://ghproxy.net/https://github.com/Logic769/news-crawler.git
```

> 注意：第三方代理会经手仓库代码。对安全性要求较高时，请优先使用官方 GitHub 地址，
> 或在克隆后核对仓库提交与 Release 信息。

### Python 依赖加速

使用清华大学 PyPI 镜像：

Windows PowerShell（在运行 `deploy.bat` 之前设置）：

```powershell
$env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
.\deploy.bat
```

Linux / macOS：

```bash
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
./deploy.sh
```

### Playwright Chromium 下载加速

没有 Chrome/Chromium 时，部署脚本会自动下载 Playwright Chromium。国内下载慢时使用 npmmirror：

Windows PowerShell：

```powershell
$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"
.\deploy.bat
```

Linux / macOS：

```bash
export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
./deploy.sh
```

也可以同时设置两个变量，一次性加速依赖安装和浏览器下载。

---

## 快速开始

### Windows

打开 PowerShell，依次执行：

```powershell
git clone https://github.com/Logic769/news-crawler.git
cd news-crawler
.\deploy.bat
```

看到以下输出表示部署成功：

```text
SMOKE TEST PASSED
部署完成。
```

### Linux / macOS

```bash
git clone https://github.com/Logic769/news-crawler.git
cd news-crawler
chmod +x deploy.sh
./deploy.sh
```

部署脚本会自动完成：

1. 检查 Python 版本；
2. 创建 `.venv` 虚拟环境；
3. 安装项目依赖；
4. 检测 Chrome/Chromium，没有则安装 Playwright Chromium；
5. 初始化 `news.db`；
6. 运行冒烟测试。

---

## 快速体验：抓取单篇文章

```powershell
cd news-crawler
.\.venv\Scripts\python.exe -X utf8 url_extract.py "https://example.com/article.html"
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

> Linux/macOS 用户把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python` 即可。

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

```powershell
.\.venv\Scripts\python.exe -X utf8 news_crawler.py test --config sources.local.json --name 你的站点标识
```

如果输出为空、标题重复或日期不对，需要调整该站点的 XPath。

### 第 4 步：运行批量抓取

```powershell
.\.venv\Scripts\python.exe -X utf8 news_crawler.py run --config sources.local.json --days 3
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

结果默认保存在项目目录的 `news.db` 中。

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

也可以用图形工具 [DB Browser for SQLite](https://sqlitebrowser.org/) 打开 `news.db`。

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

### 1. 提示找不到 python

Windows 上安装 Python 时勾选 “Add Python to PATH”，安装后重新打开 PowerShell。

### 2. 提示无法执行脚本 / 执行策略受限

直接运行 `deploy.bat`，脚本内部已经使用 `-ExecutionPolicy Bypass`，一般不会受 PowerShell 执行策略限制。

### 3. 抓取后正文为空，或者只有“正在加载”

说明目标页面正文由 JavaScript 动态加载。工具会尝试自动回退到 Chrome 渲染；如果仍然失败，请确认本机安装了 Chrome，或者已经运行过 Playwright Chromium 安装。

### 4. 站点配置里的真实网址会被上传吗

不会。只要把站点配置放在 `sources.local.json`，该文件已被 `.gitignore` 忽略。

### 5. 一个站点抓取出错会影响其他站点吗

不会。单个文章异常会被记录为 `error`，其他文章和站点会继续抓取。

### 6. 如何更新项目

```powershell
git pull
.\deploy.bat
```

---

## 安全与合规建议

- 不要提交任何网站后台地址、API Token、数据库密码或账号信息；
- 抓取公开网站前，请阅读目标网站的 `robots.txt`、服务条款和访问频率要求；
- 请合理设置 `delay`，避免对目标网站造成压力；
- 本站点配置、数据库和虚拟环境都属于本地数据，不要误提交到 Git。

---

## 相关链接

- [本项目仓库](https://github.com/Logic769/news-crawler)
- [原项目 GeneralNewsExtractor](https://github.com/kingname/GeneralNewsExtractor)
