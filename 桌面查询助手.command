#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${ZQC_WORKER_TOKEN:-}" ]; then
  echo "请先设置云端桌面助手密钥：export ZQC_WORKER_TOKEN='云端配置的同一密钥'"
  exit 1
fi
SERVER="${ZQC_WORKER_SERVER:-http://zhengqi-ai-workflow-platform.winsun.net.cn}"
exec ./.venv/bin/python3 scripts/qcc_desktop_worker.py --server "$SERVER" --token "$ZQC_WORKER_TOKEN"
