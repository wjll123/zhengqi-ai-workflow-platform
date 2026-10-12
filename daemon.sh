#!/usr/bin/env bash
# 政企存量平台 · 守护进程（服务被杀后自动拉起）
cd "$(dirname "$0")"

PORT="${ZQC_PORT:-8848}"
LOG="data/logs/daemon.log"
mkdir -p data/logs

case "$1" in
  stop)
    pkill -f "app.web_app" 2>/dev/null
    pkill -f "daemon.sh" 2>/dev/null
    echo "已停止"
    exit 0
    ;;
esac

echo "守护启动 (PID $$)" | tee -a "$LOG"
echo "  服务被杀后会自动重启；停止： ./daemon.sh stop"

while true; do
  if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ./.venv/bin/python3 -m app.web_app >> data/logs/server.log 2>&1 &
    APP=$!
    echo "[$(date '+%H:%M:%S')] 拉起服务 PID=$APP" | tee -a "$LOG"
    wait $APP 2>/dev/null
    echo "[$(date '+%H:%M:%S')] 服务退出，2 秒后重试" | tee -a "$LOG"
    sleep 2
  else
    sleep 3
  fi
done
