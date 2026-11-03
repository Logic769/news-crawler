"""冒烟测试共用的新闻样本。

镜像不携带 tests/ 下的上游样本（见 .dockerignore），容器内的 smoke_test.py
与 api_smoke.py 因此统一走这里：宿主机优先读取真实上游页面，样本缺失时回退
到内置的最小新闻页，保证 GNE 抽取路径仍然被真实执行。
"""

from __future__ import annotations

from pathlib import Path

SAMPLE_RELATIVE_PATH = Path("tests") / "163" / "1.html"

# 结构完整的新闻页：标题、发布时间、作者、多段正文、一张协议相对地址的图片。
# 图片刻意写成 // 开头，用来同时验证 normalize_images 的补全逻辑。
BUILTIN_SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>内置样本：容器冒烟测试用新闻页</title></head>
<body>
<div class="nav"><a href="/">首页</a> <a href="/news">新闻</a></div>
<div class="article">
  <h1>内置样本：容器冒烟测试用新闻页</h1>
  <div class="info"><span class="time">2026-10-01 09:30:00</span> 来源：冒烟测试</div>
  <div class="content">
    <p>这一段用于验证正文抽取在容器内仍然可用。镜像不携带上游测试样本，因此这里内置了一个最小但结构完整的新闻页，包含标题、发布时间与多段正文。</p>
    <p>第二段继续提供足够的文本密度，让基于密度的正文算法能够稳定选中正文节点，而不是被导航栏或页脚干扰。</p>
    <p>第三段用于确认段落换行在原始文本中得以保留，同时保证内容长度超过冒烟测试要求的最小值。</p>
    <img src="//cdn.example.com/smoke.jpg" alt="示例图片">
  </div>
</div>
</body>
</html>
"""


def load_sample_html(root: Path) -> str:
    """优先返回上游样本正文，缺失时回退内置页面（容器内没有 tests/）。"""
    path = Path(root) / SAMPLE_RELATIVE_PATH
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    return BUILTIN_SAMPLE_HTML
