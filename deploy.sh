#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "错误：未找到 $PYTHON_BIN，请先安装 Python 3.10 或更高版本。"
    exit 1
fi

echo "检查 Python 版本..."
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "错误：需要 Python 3.10 或更高版本。"
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "创建虚拟环境..."
    "$PYTHON_BIN" -m venv .venv
fi

VENV_PY=".venv/bin/python"

echo "安装项目依赖..."
"$VENV_PY" -m pip install -e ".[browser]"

has_chrome() {
    command -v google-chrome >/dev/null 2>&1 || \
    command -v google-chrome-stable >/dev/null 2>&1 || \
    command -v chromium >/dev/null 2>&1 || \
    command -v chromium-browser >/dev/null 2>&1
}

if [ "${SKIP_BROWSER:-0}" != "1" ] && ! has_chrome; then
    echo "未检测到 Chrome/Chromium，安装 Playwright Chromium..."
    "$VENV_PY" -m playwright install chromium
fi

echo "初始化数据库..."
"$VENV_PY" -X utf8 news_crawler.py init

echo "运行冒烟测试..."
"$VENV_PY" -X utf8 smoke_test.py

echo ""
echo "部署完成。运行示例："
echo "  .venv/bin/python -X utf8 news_crawler.py run --config sources.local.json --days 3"
