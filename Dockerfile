# 政企存量平台 · Docker 部署（全离线版）
# 特性：系统依赖(deb) + Python 依赖(whl) 全部从仓库内离线目录安装，构建机无需任何网络。
# 月报已改为 LibreOffice 无头重算（不再依赖 Node / @oai/artifact-tool 私有包）。
#
# ⚠️ 基础镜像必须锁定 Debian 版本：vendor_debs/ 的 deb 是 bookworm(12)/amd64 编译的，
#    与基础镜像 glibc 必须匹配。裸 tag `python:3.11-slim` 会随上游漂移到 trixie(13)，导致 deb 装不上。
#    故显式锁 `python:3.11-slim-bookworm`。升级基础镜像时须同步重下对应版本的 deb。
FROM python:3.11-slim-bookworm

# ---- 系统依赖（全离线：本地 deb + dpkg 安装，不访问 apt 源）----
# vendor_debs/ 含 bookworm(12)/amd64 递归依赖：git/curl/xz 等基础工具 + LibreOffice(headless 计算)。
# 安装策略：多轮 dpkg -i（解决依赖顺序；base 镜像已内置的包 dpkg 会识别为已装/升级），
# 离线无 apt 源故不能用 apt-get install -f 补依赖；末尾 configure 并强制校验。
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
    test -x /usr/lib/libreoffice/program/soffice && echo "soffice OK"

WORKDIR /app

# ---- ① 离线安装 Python 依赖（不联网）----
# offline_packages/ 由本机 `pip download` 预生成（py311 / manylinux2014_x86_64），
# requirements-lock.txt 与之严格对应（含全部传递依赖）。
COPY offline_packages/ ./offline_packages/
COPY requirements-lock.txt .
RUN pip install --no-index --find-links=./offline_packages --upgrade pip \
    && pip install --no-index --find-links=./offline_packages -r requirements-lock.txt

# ---- ② 应用代码 ----
COPY . .

# 清理构建期资产，避免离线包（whl + deb）残留进运行镜像
RUN rm -rf /app/offline_packages /app/vendor_debs

# ---- 运行时环境 ----
# 月报：LibreOffice 无头重算（app/workflows/monthly_4plus2.py 读 ZQC_SOFFICE）
ENV ZQC_SOFFICE=/usr/lib/libreoffice/program/soffice
ENV ZQC_DATA_DIR=/tmp/zqc-data
ENV ZQC_PUBLIC_DEPLOYMENT=1
ENV ZQC_HOST=0.0.0.0

# 访问鉴权（HTTP Basic）：默认 admin/bgsnhr（写在 app/core/config.py，开箱即用）。
# 如需改账号/密码，运行时注入环境变量覆盖即可：
#   docker run -e ZQC_AUTH_USER=xxx -e ZQC_AUTH_PASS=yyy ...
# 如需完全关闭鉴权：显式传空，例如 -e ZQC_AUTH_PASS=""
# 注意：此处勿写 ENV ZQC_AUTH_*=""，否则会把代码里的默认值覆盖为空导致鉴权失效。

ENTRYPOINT ["/app/entrypoint.sh"]
