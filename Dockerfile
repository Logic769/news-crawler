# syntax=docker/dockerfile:1
# news-crawler 容器镜像
# 构建：
#   docker build -t news-crawler:local .
#   # 国内加速：docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t news-crawler:local .
# 构建参数：
#   PIP_INDEX_URL            PyPI 镜像地址，默认官方源
#   INSTALL_BROWSER=1        默认值，安装 Playwright Chromium（默认镜像自带浏览器，用于 JS 动态页面与视频页）；设为 0 可构建更小的精简镜像但丢失浏览器能力
#   PLAYWRIGHT_DOWNLOAD_HOST  Playwright 浏览器下载镜像（国内可用 https://npmmirror.com/mirrors/playwright）

# 基础镜像按 digest 固定，保证可复现构建（升级时手动更新 digest）
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG INSTALL_BROWSER=1
ARG PLAYWRIGHT_DOWNLOAD_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai

WORKDIR /app

# 运行时基础依赖（时区数据让 TZ 生效；ca-certificates 供 https）
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# 先拷贝依赖清单，利用 Docker 层缓存
COPY pyproject.toml pypi_desc.md uv.lock ./
COPY docker/constraints.txt ./docker/constraints.txt
COPY gne ./gne
# 用 docker/constraints.txt（版本源自 uv.lock）锁定依赖，确保可复现构建
RUN pip install --no-cache-dir --constraint docker/constraints.txt ".[browser]"

# Playwright Chromium + 浏览器所需系统库（仅 INSTALL_BROWSER=1 时安装）
RUN if [ "${INSTALL_BROWSER}" = "1" ]; then \
      if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST}" ]; then export PLAYWRIGHT_DOWNLOAD_HOST; fi ; \
      python -m playwright install --with-deps chromium ; \
      rm -rf /var/lib/apt/lists/* ; \
    fi

# 构建期尝试启动一次 Chromium；失败仅告警，由 CI 的 docker-build-full 兜底验证
RUN if [ "${INSTALL_BROWSER}" = "1" ]; then \
      python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage']); b.close(); p.stop(); print('chromium launch OK')" || echo 'WARNING: chromium self-check failed (CI will verify)' ; \
    fi

# 拷贝应用代码
COPY . .

# 统一换行为 LF 并加可执行位（兼容从 Windows 检出时的 CRLF）
RUN sed -i 's/\r$//' /app/docker/entrypoint.sh \
 && chmod +x /app/docker/entrypoint.sh

# 说明：容器以 root 运行，避免宿主机 bind mount（./data、配置）在
# Linux/Windows 之间出现 UID 权限不一致；如对安全有更高要求，可自行
# 改用非 root 用户 + 命名卷。

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["run"]
