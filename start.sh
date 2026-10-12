#!/usr/bin/env bash
# 政企存量平台 · 启动（默认仅本机访问；局域网访问需显式设置 ZQC_HOST）
cd "$(dirname "$0")"
export ZQC_HOST="${ZQC_HOST:-127.0.0.1}"
export ZQC_PORT="${ZQC_PORT:-8848}"

if [ ! -x ".venv/bin/python3" ]; then
  echo "首次运行，创建环境（约 30 秒）..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo "============================================================"
echo "  政企存量平台"
echo "  ────────────────────────────────────────────────────────"
echo "  本机访问  : http://127.0.0.1:$ZQC_PORT"
[ "$ZQC_HOST" = "0.0.0.0" ] && [ -n "$IP" ] && echo "  局域网访问: http://$IP:$ZQC_PORT"
echo "  ────────────────────────────────────────────────────────"
echo "  保持本窗口开着；Ctrl+C 停止"
echo "============================================================"
echo
exec ./.venv/bin/python3 -m app.web_app
