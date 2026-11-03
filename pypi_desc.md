# news-crawler

news-crawler 是一个面向多站点新闻抓取的工具，内置重构版 GNE 新闻抽取核心库。输入新闻页面 HTML，可提取标题、作者、发布时间、正文、图片和 meta 信息；支持列表页抓取、浏览器渲染回退、SQLite 入库以及 FastAPI /v1 接口。

## 安装

```bash
pip install -e ".[browser,api]"
```

## 文档

https://github.com/Logic769/news-crawler/blob/master/README.md
