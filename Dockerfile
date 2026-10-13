# 政企存量平台 · Render Docker 部署
# 与 python runtime 版的区别：额外内置 Node 24 + 私有 @oai/artifact-tool，
# 让「政企存量 4+2 收客保月报」也能在公开链接上计算（落责数据更新/落责审计等其余任务本就用 openpyxl/python-pptx，照常可用）。

FROM python:3.11-slim

# ---- 系统依赖 ----
# git：构建期从私有仓库拉取已打包的 artifact-tool。
# libcairo 等：artifact-tool 嵌套依赖 skia-canvas 的运行时库（月报不触发渲染，但保留以防边缘路径）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg git \
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

# ---- 从私有 Git 仓库拉取已打包的 @oai/artifact-tool（纯 JS/wasm，跨平台）----
# 该包为 OpenAI Codex 内部私有包，无公开 npm 源；方案改为搬运你本机已装好的目录。
# 本地打包时务必删除嵌套的 node_modules/skia-canvas（macOS 原生 .node 在 Linux 上不可用，
# 且月报只用到公式引擎 wasm，不触发渲染，删之无影响）。
# 下列两个变量请在 Render 后台「Environment」中设置（构建期可用，令牌不落盘到镜像）：
#   ARTIFACT_GIT_URL    = https://github.com/<you>/artifact-tool-private.git
#   ARTIFACT_GIT_TOKEN  = <私有仓库只读 PAT>
ARG ARTIFACT_GIT_URL
ARG ARTIFACT_GIT_TOKEN
RUN set -eux; \
    if [ -n "$ARTIFACT_GIT_URL" ] && [ -n "$ARTIFACT_GIT_TOKEN" ]; then \
      mkdir -p /opt/artifact/node_modules/@oai; \
      CLONE_URL=$(echo "$ARTIFACT_GIT_URL" | sed -E "s#(https?://)#\1oauth2:${ARTIFACT_GIT_TOKEN}@#"); \
      rm -rf /tmp/artifact-tool && \
      git clone --depth 1 "$CLONE_URL" /tmp/artifact-tool 2>/dev/null && \
      cp -R /tmp/artifact-tool /opt/artifact/node_modules/@oai/artifact-tool && \
      rm -rf /opt/artifact/node_modules/@oai/artifact-tool/node_modules/skia-canvas && \
      echo "OK: artifact-tool 已就位（已移除 skia-canvas 原生模块）"; \
    else \
      echo "WARN: 未提供 ARTIFACT_GIT_URL / ARTIFACT_GIT_TOKEN，月报（4+2 收客保月报）将运行时降级，其余任务正常。"; \
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
