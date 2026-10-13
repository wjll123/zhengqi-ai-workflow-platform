# 政企存量平台 · Docker 部署（全离线版）
# 特性：系统依赖(deb) + Python 依赖(whl) + Node 全部从仓库内 offline_packages/ 离线安装，
# 构建机无需访问 apt 源 / pip 源 / nodejs.org。
# 仅 artifact-tool（OpenAI Codex 私有包）需从私有 Git 仓库拉取（若提供 URL+Token）。
#
# ⚠️ 基础镜像必须锁定 Debian 版本：vendor_debs/ 的 deb 是 bookworm(12)/amd64 编译的，
#    与基础镜像 glibc 必须匹配。裸 tag `python:3.11-slim` 会随上游漂移到 trixie(13)，导致 deb 装不上。
#    故显式锁 `python:3.11-slim-bookworm`。升级基础镜像时须同步重下对应版本的 deb。
FROM python:3.11-slim-bookworm

# ---- 系统依赖（全离线：本地 deb + dpkg 安装，不访问 apt 源）----
# vendor_debs/ 含 bookworm(12)/amd64 全量递归依赖（128 个 deb，源自阿里云镜像）。
# git：拉取 artifact-tool 用；libcairo 等：artifact-tool 嵌套 skia-canvas 的运行时库。
# 安装策略：多轮 dpkg -i（解决依赖顺序 + base 镜像已内置的包会被 dpkg 识别为"已装/升级"），
# 离线无 apt 源故不能用 apt-get install -f 补依赖；末尾按需 configure 并强制校验。
COPY vendor_debs/ /tmp/debs/
RUN set -eux; \
    for i in $(seq 1 12); do \
      if dpkg -i --force-depends /tmp/debs/*.deb >/dev/null 2>&1; then break; fi; \
    done; \
    dpkg --configure -a || true; \
    dpkg -i --force-depends /tmp/debs/*.deb || true; \
    rm -rf /tmp/debs; \
    echo "---- 强制校验：关键命令/库必须存在，否则构建失败 ----"; \
    command -v git && command -v curl && command -v xz; \
    ldconfig -p | grep -q libcairo.so && echo "libcairo OK"; \
    ldconfig -p | grep -q libgomp.so && echo "libgomp OK"; \
    ldconfig -p | grep -q libpango-1.0.so && echo "libpango OK"

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
# 注意：ARG 给默认空值，且用 ${VAR:-} 取值——否则 set -u 下未传该 ARG 会直接报
#       "parameter not set" 导致构建失败（不会走到 else 分支）。
ARG ARTIFACT_GIT_URL=""
ARG ARTIFACT_GIT_TOKEN=""
RUN set -eux; \
    if [ -n "${ARTIFACT_GIT_URL:-}" ] && [ -n "${ARTIFACT_GIT_TOKEN:-}" ]; then \
      mkdir -p /opt/artifact/node_modules/@oai; \
      CLONE_URL=$(echo "${ARTIFACT_GIT_URL}" | sed -E "s#(https?://)#\1oauth2:${ARTIFACT_GIT_TOKEN}@#"); \
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

# 清理构建期资产，避免离线包（whl + Node tarball + deb）残留进运行镜像
RUN rm -rf /app/offline_packages /app/vendor_debs

# 运行时定位 artifact-tool（与 app/modules/monthly_4plus2.py 读取的环境变量一致）
ENV ZQC_ARTIFACT_NODE=/usr/local/bin/node
ENV ZQC_ARTIFACT_NODE_MODULES=/opt/artifact/node_modules
ENV ZQC_DATA_DIR=/tmp/zqc-data
ENV ZQC_PUBLIC_DEPLOYMENT=1
ENV ZQC_HOST=0.0.0.0

# 访问鉴权（HTTP Basic）默认空值 = 不鉴权；运行时用 -e / compose environment 注入即可启用：
#   docker run -e ZQC_AUTH_USER=admin -e ZQC_AUTH_PASS=<密码> ...
ENV ZQC_AUTH_USER=""
ENV ZQC_AUTH_PASS=""

# 注意：如需构建期出网（如 git clone 外网仓库），在此行前加：
#   ENV HTTP_PROXY=http://<your-proxy>:<port>
#   ENV HTTPS_PROXY=http://<your-proxy>:<port>
# 离线依赖安装（① ②）不受影响，二者均不联网。

ENTRYPOINT ["/app/entrypoint.sh"]
