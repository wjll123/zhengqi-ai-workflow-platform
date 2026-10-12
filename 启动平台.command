#!/usr/bin/env bash
# ==========================================================
#   政企存量平台 · 一键启动（双击即可，自动守护）
# ==========================================================
cd "$(dirname "$0")"
PORT=8848

clear
echo "============================================================"
echo "                政 企 存 量 平 台"
echo "============================================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "✕ 未找到 python3，请先安装 Python 3"
  read -r _; exit 1
fi

if [ ! -x ".venv/bin/python3" ]; then
  echo "首次运行，准备环境（约 30 秒）..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
  echo "✓ 环境就绪"; echo
fi

# 清理旧进程
pkill -f "app.web_app" 2>/dev/null
sleep 1

echo "  访问地址： http://127.0.0.1:$PORT"
echo "  ──────────────────────────────────────────────────"
echo "  ▸ 本窗口保持开启 = 服务持续运行（守护模式）"
echo "  ▸ 关闭本窗口 = 停止服务"
echo "  ▸ 服务意外退出会自动重启"
echo "============================================================"
echo

# 延迟打开浏览器
( sleep 3; open "http://127.0.0.1:$PORT" ) &

# 守护循环：服务挂了自动拉起
while true; do
  if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ZQC_HOST=127.0.0.1 ZQC_PORT=$PORT ./.venv/bin/python3 -m app.web_app \
      >> data/logs/server.log 2>&1 &
    APP=$!
    wait $APP 2>/dev/null
    echo "[$(date '+%H:%M:%S')] 服务退出，2 秒后重启..."
    sleep 2
  else
    sleep 3
  fi
done
