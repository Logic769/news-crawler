#!/usr/bin/env bash
# news-crawler Docker 一键部署（Linux / macOS）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v docker >/dev/null 2>&1; then
    echo "错误：未找到 docker。请先安装 Docker（https://docs.docker.com/get-docker/）并确保 Docker Desktop / 守护进程已启动。"
    exit 1
fi

DC="docker compose"
if ! docker compose version >/dev/null 2>&1; then
    echo "错误：需要 Docker Compose v2（docker compose 插件）。请升级 Docker Desktop 或安装 Compose v2。"
    exit 1
fi

if [ ! -f sources.local.json ]; then
    echo "未找到 sources.local.json，已从 sources.json 模板创建。"
    echo "请按 README 填写站点配置后再执行抓取。"
    cp -f sources.json sources.local.json
fi

mkdir -p data

echo "构建镜像..."
$DC build

echo "初始化数据库..."
$DC run --rm crawler init

echo "运行冒烟测试..."
$DC run --rm crawler smoke

echo ""
echo "Docker 部署完成。常用命令："
echo "  抓取：      $DC run --rm crawler run --config sources.local.json --days 3"
echo "  测试解析：  $DC run --rm crawler test --config sources.local.json --name <站点名>"
echo "  进入容器：  $DC run --rm crawler shell"
echo "  数据库文件：./data/news.db"
