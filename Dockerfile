# 政企存量平台 · Docker 部署（离线依赖版）
# 特性：pip 依赖 + Node 均从仓库内 offline_packages/ 离线安装，构建机无需公网 pip/node。
# 仅 artifact-tool（OpenAI Codex 私有包）需从私有 Git 仓库拉取。

FROM python:3.11-slim

# ---- 系统依赖 ----
# git：构建期从私有仓库拉取已打包的 artifact-tool（唯一需要网络的步骤，见文末 no_proxy）。
# libcairo 等：artifact-tool 嵌套 skia-canvas 的运行时库（月报不触发渲染，保留以防边缘路径）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg git xz-utils \
    libcairo2 libjpeg62-turbo libpango-1.0-0 libpangocairo-1.0-0 \
    libgif7 libgomp1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- ① 离线安装 Python 依赖（不联网）----
# offline_packages/ 由本机 `pip download` 预生成（py311 / manylinux2014_x86_64），
# requirements-lock.txt 与之严格对应（含全部传递依赖）。
COPY offline_packages/ ./offline_packages/
COPY requirements-lock.txt .
RUN pip install --no-index --find-links=./offline_packages --upgrade pip \
    && pip install --no-index --find-links=./offline_packages -r requirements-lock.txt

# ---- ② 离线安装 Node 24（解压官方 Linux tarball，不联网）----
# offline_packages/node-v24.10.0-linux-x64.tar.xz 为官方 nodejs.org 发行版。
# 已在上一步 COPY offline_packages/ 进入构建上下文，这里直接解压并清理。
RUN tar -xJf ./offline_packages/node-v24.10.0-linux-x64.tar.xz -C /opt \
    && mv /opt/node-v24.10.0-linux-x64 /opt/node \
    && ln -sf /opt/node/bin/node /usr/local/bin/node \
    && ln -sf /opt/node/bin/npm /usr/local/bin/npm \
    && node --version

# ---- ③ 从私有 Git 仓库拉取已打包的 @oai/artifact-tool（纯 JS/wasm，跨平台）----
# 该包为 OpenAI Codex 内部私有包，无公开 npm 源；方案为搬运本机已装好的目录。
# 本地打包时务必删除嵌套的 node_modules/skia-canvas（macOS 原生 .node 在 Linux 不可用，
# 且月报只用公式引擎 wasm，不触发渲染，删之无影响）。
# 构建变量（构建期可用，令牌不落盘到镜像）：
#   ARTIFACT_GIT_URL    = 私有仓库 URL（内网 GitLab 或外网 GitHub；内网地址自动直连不走代理）
#   ARTIFACT_GIT_TOKEN  = 私有仓库只读 PAT / oauth2 密码
# 唯一需要网络的步骤：若构建环境在公网墙内，请在下方 RUN 前按需注入代理（见注释）。
ARG ARTIFACT_GIT_URL
ARG ARTIFACT_GIT_TOKEN
RUN set -eux; \
    if [ -n "$ARTIFACT_GIT_URL" ] && [ -n "$ARTIFACT_GIT_TOKEN" ]; then \
      mkdir -p /opt/artifact/node_modules/@oai; \
      CLONE_URL=$(echo "$ARTIFACT_GIT_URL" | sed -E "s#(https?://)#\1oauth2:${ARTIFACT_GIT_TOKEN}@#"); \
      export no_proxy="172.18.101.172,172.18.101.170,localhost,127.0.0.1"; \
      rm -rf /tmp/artifact-tool && \
      git clone --depth 1 "$CLONE_URL" /tmp/artifact-tool 2>/dev/null && \
      cp -R /tmp/artifact-tool /opt/artifact/node_modules/@oai/artifact-tool && \
      rm -rf /opt/artifact/node_modules/@oai/artifact-tool/node_modules/skia-canvas && \
      echo "OK: artifact-tool 已就位（已移除 skia-canvas 原生模块）"; \
    else \
      echo "WARN: 未提供 ARTIFACT_GIT_URL / ARTIFACT_GIT_TOKEN，月报（4+2 收客保月报）将运行时降级，其余任务正常。"; \
    fi

# ---- ④ 应用代码 ----
COPY . .

# 清理构建期资产，避免离线包（含 32MB Node tarball）残留进运行镜像
RUN rm -rf /app/offline_packages

# 运行时定位 artifact-tool（与 app/modules/monthly_4plus2.py 读取的环境变量一致）
ENV ZQC_ARTIFACT_NODE=/usr/local/bin/node
ENV ZQC_ARTIFACT_NODE_MODULES=/opt/artifact/node_modules
ENV ZQC_DATA_DIR=/tmp/zqc-data
ENV ZQC_PUBLIC_DEPLOYMENT=1
ENV ZQC_HOST=0.0.0.0

# 注意：如需构建期出网（如 git clone 外网仓库），在此行前加：
#   ENV HTTP_PROXY=http://<your-proxy>:<port>
#   ENV HTTPS_PROXY=http://<your-proxy>:<port>
# 离线依赖安装（① ②）不受影响，二者均不联网。

ENTRYPOINT ["/app/entrypoint.sh"]
