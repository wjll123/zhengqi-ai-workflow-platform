#!/usr/bin/env bash
# ==========================================================
#      政企存量平台 · 一键运行（无需打开网页）
#      把源数据放进 data/inbox/ 后，双击本文件即可
# ==========================================================
cd "$(dirname "$0")"

clear
echo "============================================================"
echo "                政 企 存 量 平 台"
echo "============================================================"
echo

if [ ! -x ".venv/bin/python3" ]; then
  echo "首次运行，准备环境中（约 30 秒）..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

echo "源数据目录: data/inbox/"
ls data/inbox/*.xls* data/inbox/*.csv 2>/dev/null | sed 's|.*/|  · |' || echo "  （空）"
echo
echo "------------------------------------------------------------"
echo " 开始处理..."
echo "------------------------------------------------------------"
echo

./.venv/bin/python3 -m app.cli run-all

echo
echo "------------------------------------------------------------"
echo " 成品已生成到:"
echo "   $(pwd)/data/outputs/"
echo "------------------------------------------------------------"
ls -1 data/outputs/*.xlsx data/outputs/*.txt 2>/dev/null | sed 's|.*/|  ✓ |'
echo
echo "按回车键关闭窗口（或直接拖拽 outputs 里的文件使用）..."
read -r _
open data/outputs
