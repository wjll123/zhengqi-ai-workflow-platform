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
