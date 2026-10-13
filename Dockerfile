# 政企存量平台 · Render Docker 部署
# 与 python runtime 版的区别：额外内置 Node 24 + 私有 @oai/artifact-tool，
# 让「政企存量 4+2 收客保月报」也能在公开链接上计算（落责数据更新/落责审计等其余任务本就用 openpyxl/python-pptx，照常可用）。

FROM python:3.11-slim

# 内网代理（构建时需要访问外网下载依赖）
ENV HTTP_PROXY=http://172.18.101.170:3128
ENV HTTPS_PROXY=http://172.18.101.170:3128

# ---- 系统依赖 ----
# skia-canvas（artifact-tool 的间接原生依赖）在 Linux 上需要这些运行时库。
# 若 npm install 仍触发 skia-canvas 源码编译，可临时在下方 npm install 后加 --ignore-scripts
# （月报只用到 artifact-tool 的 Excel 公式引擎 wasm，不触发 skia-canvas 渲染，通常不影响）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    libcairo2 libjpeg62-turbo libpango-1.0-0 libpangocairo-1.0-0 \
    libgif7 libgomp1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# ---- 安装 Node 24（artifact-tool 运行所需，.mjs 用 node 调用）----
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(command -v node)" /usr/local/bin/node

WORKDIR /app

# ---- Python 依赖 ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 通过私有 npm 源安装 @oai/artifact-tool ----
# 下列两个变量请在 Render 后台「Environment」中设置（构建期可用，令牌不落盘到镜像外）：
#   NPM_REGISTRY_URL = https://<你的私有registry>/<path>/
#   NPM_TOKEN        = <私有源访问令牌>
# 若私有源未提供 Linux 原生预编译的 skia-canvas，可改成 `npm install @oai/artifact-tool --ignore-scripts`。
ARG NPM_REGISTRY_URL
ARG NPM_TOKEN
RUN set -eux; \
    if [ -n "$NPM_REGISTRY_URL" ] && [ -n "$NPM_TOKEN" ]; then \
      npm config set @oai:registry "$NPM_REGISTRY_URL"; \
      AUTH_HOST=$(echo "$NPM_REGISTRY_URL" | sed -E 's#^https?://##; s#/.*##'); \
      AUTH_PATH=$(echo "$NPM_REGISTRY_URL" | sed -E 's#^https?://##; s#^[^/]+##; s#/+$##'); \
      npm config set "//${AUTH_HOST}${AUTH_PATH}:_authToken" "$NPM_TOKEN"; \
      mkdir -p /opt/artifact && cd /opt/artifact && npm init -y >/dev/null 2>&1 \
        && npm install @oai/artifact-tool || echo "WARN: @oai/artifact-tool 安装失败（私有源不可达或令牌错误），月报将运行时降级，其余任务不受影响。"; \
    else \
      echo "WARN: 未提供 NPM_REGISTRY_URL / NPM_TOKEN，月报（4+2 收客保月报）将不可用，其余任务正常。"; \
    fi

# ---- 应用代码 ----
COPY . .

# 运行时定位 artifact-tool 的位置（与 app/modules/monthly_4plus2.py 读取的环境变量一致）
ENV ZQC_ARTIFACT_NODE=/usr/local/bin/node
ENV ZQC_ARTIFACT_NODE_MODULES=/opt/artifact/node_modules
ENV ZQC_DATA_DIR=/tmp/zqc-data
ENV ZQC_PUBLIC_DEPLOYMENT=1
ENV ZQC_HOST=0.0.0.0

ENTRYPOINT ["/app/entrypoint.sh"]
