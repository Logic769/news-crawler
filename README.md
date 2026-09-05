# 新闻抓取工具（基于 GNE）

这是在 GNE 新闻正文抽取器基础上扩展的一套抓取工具，用来从不同网站上抓取新闻标题和正文，并按时间范围筛选。

## 文件说明

- `news_crawler.py`：多站点批量抓取主程序，负责列表解析、3 天时间过滤、正文抽取、SQLite 入库和去重。
- `url_extract.py`：单条网址抽取工具，输入一篇文章网址，输出 JSON。
- `sources.json`：仓库自带的空站点配置模板；若直接修改并提交，站点配置会公开。
- `sources.local.json`：本地私人站点配置，已被 `.gitignore` 忽略，不会提交；存在时程序会自动优先使用。
- `news.db`：SQLite 数据库（首次运行 `init` 后自动创建）。

## 全新部署

Windows PowerShell：

```powershell
git clone https://github.com/Logic769/news-crawler.git
cd news-crawler
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
```

Linux / macOS：

```bash
git clone https://github.com/Logic769/news-crawler.git
cd news-crawler
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[browser]"
```

工具默认使用本机 Chrome 作为浏览器回退，若机器上没有 Chrome：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 单条网址抓取

```powershell
.\.venv\Scripts\python.exe -X utf8 url_extract.py
```

按提示粘贴文章网址，也可以直接传参：

```powershell
.\.venv\Scripts\python.exe -X utf8 url_extract.py "https://example.com/article.html"
```

输出为 JSON，包含 `title`、`publish_time`、`author`、`content`、`images`。

## 多站点批量抓取

### 1. 添加你的站点配置

仓库自带的 `sources.json` 是空模板。推荐把个人站点列表写到 `sources.local.json`（该文件已被 Git 忽略），这样提交代码时不会泄露你的站点配置：

```powershell
Copy-Item sources.json sources.local.json
```

在 `sources.local.json`（或 `sources.json`）的 `sources` 数组中添加站点。参考格式：

```json
{
  "name": "站点标识",
  "list_url": "https://example.com/news.html",
  "item_xpath": "//ul[contains(@class,'list')]//li",
  "title_xpath": ".//a",
  "url_xpath": ".//a/@href",
  "date_xpath": ".//*[contains(@class,'time')]",
  "date_format": "%Y-%m-%d",
  "timezone": "Asia/Shanghai"
}
```

字段说明：

- `name`：站点唯一标识。
- `list_url`：新闻列表页地址；如果列表分页，也可以用 `list_urls` 直接写多个页面地址。
- `item_xpath`：定位每条新闻节点的 XPath，必填。
- `title_xpath`：节点内标题 XPath，不填默认取第一个 `a` 标签。
- `url_xpath`：节点内详情页链接 XPath，不填默认取第一个 `a` 标签的 `href`。
- `date_xpath`：节点内发布日期 XPath，不填则从整条节点文本中自动识别日期。
- `date_format`：日期格式，例如 `%Y-%m-%d`、`%Y-%m-%d %H:%M`，不填也会自动尝试常见格式。
- `timezone`：站点所在时区，默认 `Asia/Shanghai`。
- `render`：设为 `true` 时列表页直接用 Chrome 渲染，适合 JS 动态列表。
- `delay`：每个请求之间的等待秒数，默认 0.5。

### 2. 测试列表解析

推荐先只测试一个站点，确认能解析出标题、链接和日期：

```powershell
.\.venv\Scripts\python.exe -X utf8 news_crawler.py test --config sources.local.json --name 站点标识
```

如果解析为空或日期不对，需要调整该站点的 XPath。

### 3. 初始化数据库

```powershell
.\.venv\Scripts\python.exe -X utf8 news_crawler.py init
```

### 4. 运行抓取

```powershell
.\.venv\Scripts\python.exe -X utf8 news_crawler.py run --config sources.local.json --days 3
```

常用参数：

- `--days 3`：抓取最近 3 天。
- `--since 2026-09-01`：从指定日期开始。
- `--workers 5`：同时处理几个站点，默认 3。
- `--limit-per-source 20`：调试时限制每个站点最多处理多少条。
- `--force`：忽略数据库中的已抓记录，强制重新抓取。

## 查看结果

抓取结果存放在 `news.db`，可用任意 SQLite 工具查询：

```sql
SELECT source, title, publish_time, url
FROM articles
WHERE status = 'ok'
ORDER BY publish_time DESC;
```

只显示最近 3 天：

```sql
SELECT source, title, publish_time, url
FROM articles
WHERE status = 'ok'
  AND datetime(publish_time) >= datetime('now', '-3 days', 'localtime')
ORDER BY publish_time DESC;
```

## 工作原理

1. 读取配置文件中每个站点的配置（默认 `sources.json`；存在 `sources.local.json` 时自动使用它）。
2. 抓取列表页 HTML，用 XPath 解析出标题、链接、发布时间。
3. 按日期过滤，只保留最近 N 天内的文章。
4. 逐条抓取文章详情页。
5. 先用 GNE 抽取标题和正文；若正文为空或页面需要 JS 渲染，自动改用无头 Chrome。
6. 结果写入 SQLite，以 `(source, url)` 去重，已抓过的文章下次自动跳过。

数据库状态字段：

- `ok`：成功抓取且发布时间在时间范围内。
- `old`：已识别为超出时间范围，不再重复抓取。
- `skip`：本次运行中数据库里已有记录。
- `no_content`：正文提取失败。
- `unknown_date`：无法解析发布日期。
- `error`：抓取过程出错。
- `list_empty`：列表页没有解析到任何条目。

## 注意事项

- 正文抽取器本身不会主动请求网页，请求逻辑在本项目脚本中实现。
- 抓取公开网页时请注意目标网站的 `robots.txt`、访问频率和内容使用条款。
- 站点越多、越复杂，主要工作越集中在逐个调试 XPath 上。
- 如果某站点列表页不显示日期，工具会先抓详情页再用 GNE 识别出的发布时间过滤，请求量会明显增加。
- 本项目基于 [GeneralNewsExtractor](https://github.com/kingname/GeneralNewsExtractor) 扩展。
