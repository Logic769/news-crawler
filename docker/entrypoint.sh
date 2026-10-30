#!/usr/bin/env bash
# news-crawler 容器统一入口
#
# 用法（在容器内/docker compose run 后面）：
#   run   [原 news_crawler.py run 参数]     默认命令，数据库写入 /app/data/news.db
#   init  [原 news_crawler.py init 参数]    初始化数据库
#   test  [原 news_crawler.py test 参数]    只测列表解析
#   smoke                                   运行离线冒烟测试
#   live-news [--static 5 --dynamic 3 --video 2]
#                                           实时新闻抓取验证（静态 / JS 渲染 / 视频）
#   shell                                   进入 bash
set -euo pipefail
cd /app

DATA_DIR="${DATA_DIR:-/app/data}"
DB_PATH="${DB_PATH:-$DATA_DIR/news.db}"
mkdir -p "$DATA_DIR"

# 防御：宿主机缺少 sources.local.json 时，Docker 可能把只读挂载点建成目录
if [ -d /app/sources.local.json ]; then
  echo "错误：/app/sources.local.json 是一个目录（通常因宿主机缺少该文件，Docker 自动创建）。请先在项目根目录创建 sources.local.json（可从 sources.json 复制）后重试。" >&2
  exit 1
fi

cmd="${1:-run}"
if [ "$#" -gt 0 ]; then
  shift
fi

case "$cmd" in
  shell|bash|sh)
    exec /bin/bash
    ;;
  smoke)
    exec python -X utf8 smoke_test.py "$@"
    ;;
  live|live-news)
    exec python -X utf8 verify_live_news.py "$@"
    ;;
  init|run)
    # 用户已显式传 --db 时尊重之，否则统一落到持久化数据目录
    has_db=0
    for arg in "$@"; do
      case "$arg" in
        --db|--db=*) has_db=1 ;;
      esac
    done
    if [ "$has_db" = "1" ]; then
      exec python -X utf8 news_crawler.py "$cmd" "$@"
    else
      exec python -X utf8 news_crawler.py "$cmd" --db "$DB_PATH" "$@"
    fi
    ;;
  test)
    exec python -X utf8 news_crawler.py test "$@"
    ;;
  *)
    exec python -X utf8 news_crawler.py "$cmd" "$@"
    ;;
esac
