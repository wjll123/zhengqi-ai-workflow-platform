#!/usr/bin/env bash
set -euo pipefail

# Build a self-contained macOS offline package. Customer data and runtime state
# are deliberately excluded from the archive.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(cd "${1:-$ROOT/dist}" 2>/dev/null && pwd || { mkdir -p "${1:-$ROOT/dist}"; cd "${1:-$ROOT/dist}" && pwd; })"
NAME="${2:-zhengqi-ai-workflow-offline-$(date +%Y%m%d)}"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/zqc-offline.XXXXXX")"
PACKAGE="$STAGE/$NAME"
WHEEL_SOURCE="${ZQC_OFFLINE_WHEELS:-$ROOT/政企ai工作流平台/wheels}"
RUNTIME_SOURCE="${ZQC_OFFLINE_RUNTIME:-$ROOT/runtime/LibreOfficeDev.app}"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

mkdir -p "$PACKAGE" "$OUT_DIR"
rsync -a \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'data/inbox/' --exclude 'data/outputs/' --exclude 'data/logs/' \
  --exclude 'data/state/' --exclude 'data/trash/' --exclude 'data/config/' --exclude 'data/archive/' \
  --exclude 'runtime/' --exclude 'offline-assets/' --exclude 'dist/' --exclude '*.zip' \
  "$ROOT/" "$PACKAGE/"

mkdir -p "$PACKAGE/data/inbox" "$PACKAGE/data/outputs" "$PACKAGE/data/logs" \
  "$PACKAGE/data/state" "$PACKAGE/data/trash" "$PACKAGE/data/config"
touch "$PACKAGE/data/inbox/.gitkeep" "$PACKAGE/data/outputs/.gitkeep" \
  "$PACKAGE/data/logs/.gitkeep" "$PACKAGE/data/state/.gitkeep" \
  "$PACKAGE/data/trash/.gitkeep" "$PACKAGE/data/config/.gitkeep"

if [[ ! -d "$WHEEL_SOURCE" ]]; then
  echo "missing wheel cache: $WHEEL_SOURCE" >&2
  exit 2
fi
mkdir -p "$PACKAGE/wheels"
rsync -a "$WHEEL_SOURCE/" "$PACKAGE/wheels/"

if [[ ! -x "$RUNTIME_SOURCE/Contents/MacOS/soffice" ]]; then
  echo "missing macOS LibreOffice runtime: $RUNTIME_SOURCE" >&2
  exit 2
fi
mkdir -p "$PACKAGE/runtime"
rsync -a "$RUNTIME_SOURCE" "$PACKAGE/runtime/"

cat > "$PACKAGE/install_offline.sh" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 Python 3.9+；请先安装 Python。" >&2
  exit 1
fi
"$PYTHON_BIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --no-index --find-links "$ROOT/wheels" -r "$ROOT/requirements.txt"
chmod +x "$ROOT/run_offline.sh" "$ROOT/start.sh" "$ROOT/stop.sh"
echo "离线安装完成。运行：$ROOT/run_offline.sh"
INSTALL

cat > "$PACKAGE/run_offline.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "尚未安装，请先运行 ./install_offline.sh" >&2
  exit 1
fi
export ZQC_HOST="${ZQC_HOST:-127.0.0.1}"
export ZQC_PORT="${ZQC_PORT:-8848}"
if [[ -x "$ROOT/runtime/LibreOfficeDev.app/Contents/MacOS/soffice" ]]; then
  export ZQC_SOFFICE="$ROOT/runtime/LibreOfficeDev.app/Contents/MacOS/soffice"
fi
exec "$ROOT/.venv/bin/python" -m app.web_app
RUN

cat > "$PACKAGE/OFFLINE_INSTALL.md" <<'DOC'
# macOS 离线安装

1. 在目标 Mac 安装 Python 3.9 或更高版本。
2. 解压本目录，运行 `./install_offline.sh`。安装过程不会访问网络。
3. 运行 `./run_offline.sh`，浏览器打开 `http://127.0.0.1:8848`。
4. 停止服务可按 `Ctrl+C`，或运行 `./stop.sh`。

本包针对 macOS Apple Silicon 构建，包含 Python wheel 缓存和 LibreOffice。企查查查询支持两种方式：

- 本机模式：平台和已登录浏览器在同一台 Mac 上运行。
- 桌面助手模式：云端平台派发工单，这台 Mac 运行 `桌面查询助手.command`，主动领取工单并使用本机已登录浏览器查询。桌面助手配置见 `docs/企查查桌面助手部署.md`；需要设置 `ZQC_WORKER_TOKEN`、`ZQC_WORKER_SERVER`，以及启用平台鉴权时的 `ZQC_AUTH_USER`、`ZQC_AUTH_PASS`。

Chrome/Edge 需要调试模式，Safari 可直接使用。验证码和风控验证仍由使用者在本机浏览器中处理。
DOC

chmod +x "$PACKAGE/install_offline.sh" "$PACKAGE/run_offline.sh" "$PACKAGE/start.sh" "$PACKAGE/stop.sh"
ARCHIVE="$OUT_DIR/$NAME.zip"
rm -f "$ARCHIVE"
(cd "$STAGE" && zip -qr "$ARCHIVE" "$NAME")
echo "$ARCHIVE"
