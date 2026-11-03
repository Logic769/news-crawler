# news-crawler

`gne` 是 [news-crawler](https://github.com/Logic769/news-crawler) 项目内置的新闻抽取核心库（GNE 重构版）。输入新闻页面 HTML，可提取标题、作者、发布时间、正文、图片和 meta 信息。

> 注意：PyPI 上的 `gne` 发行包**只包含 `gne/` 抽取库**。完整应用（`news_crawler.py`、`url_extract.py`、`api.py`、`news_api/`、CLI 与 Docker 镜像）不在 wheel 内，需要通过源码或 Docker 部署使用，用法见仓库 README。

## 安装

从源码安装时，先克隆仓库并进入项目根目录（`pip install -e` 需要当前目录存在 `pyproject.toml`）：

```bash
git clone https://github.com/Logic769/news-crawler.git
cd news-crawler
```

只安装抽取核心：

```bash
pip install -e .
```

需要浏览器回退与 API 依赖时：

```bash
pip install -e ".[browser,api]"
```

`[browser]` 只安装 Playwright 的 Python 包，浏览器本体需要另外下载（未安装时浏览器回退会启动失败）：

```bash
python -m playwright install --with-deps chromium   # Linux：同时安装系统依赖，需要 root/sudo
python -m playwright install chromium               # macOS / Windows：系统依赖通常已满足
```

## 文档

https://github.com/Logic769/news-crawler/blob/master/README.md

## 许可证

GPL-3.0-only，完整文本见仓库 [LICENSE](https://github.com/Logic769/news-crawler/blob/master/LICENSE)。
