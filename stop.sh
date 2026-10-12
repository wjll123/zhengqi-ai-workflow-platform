#!/usr/bin/env bash
# 政企存量平台 · 停止脚本
set -e

cd "$(dirname "$0")"
PIDFILE="data/platform.pid"

if [ ! -f "$PIDFILE" ]; then
  echo "未找到 PID 文件，服务可能未启动"
  exit 0
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "已停止服务 (PID $PID)"
else
  echo "进程 $PID 不存在，清理 PID 文件"
fi
rm -f "$PIDFILE"
