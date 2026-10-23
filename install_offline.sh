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
