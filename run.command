#!/usr/bin/env bash
# 政企存量平台 · 双击启动（macOS）
cd "$(dirname "$0")"
PORT="${ZQC_PORT:-8848}"

if [ ! -x ".venv/bin/python3" ]; then
  echo "首次运行，创建 Python 环境（约 30 秒）..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

echo "============================================================"
echo "  政企存量平台"
echo "  访问地址: http://127.0.0.1:$PORT"
echo "  ────────────────────────────────────────────────────────"
echo "  保持本窗口开着 = 服务运行中"
echo "  关闭本窗口 = 停止服务"
echo "============================================================"
echo

( sleep 3; open "http://127.0.0.1:$PORT" ) &

exec ./.venv/bin/python3 -m app.web_app
