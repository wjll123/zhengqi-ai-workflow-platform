#!/usr/bin/env bash
set -e

# 公开部署：运行时数据在 /tmp/zqc-data（重启/实例回收后清空），每次启动重新铺设模板与预置底稿。
mkdir -p /tmp/zqc-data/{inbox,outputs,templates,logs,config,state,trash}

# 计算模板（已提交到仓库 data/templates，命名须与代码 CALC_TEMPLATES 一致）
if [ -d /app/data/templates ]; then
  cp -n /app/data/templates/* /tmp/zqc-data/templates/ 2>/dev/null || true
fi

# 预置历史底稿（仓库 data/seed 里的聚合表，非客户原始数据）：使月报能从上月成表续写。
# 若不想把聚合表公开进仓库，删除 data/seed 即可（届时需用户在界面先跑出上月成表再跑次月）。
if [ -d /app/data/seed ]; then
  cp -n /app/data/seed/* /tmp/zqc-data/outputs/ 2>/dev/null || true
fi

# LibreOffice 无头运行准备：提供可写的 HOME 与运行时目录，避免首次运行因权限/目录缺失失败。
export HOME="${HOME:-/tmp}"
mkdir -p /tmp/.config /tmp/.cache 2>/dev/null || true
if [ -x /usr/lib/libreoffice/program/soffice ]; then
  echo "[entrypoint] LibreOffice: $(/usr/lib/libreoffice/program/soffice --version 2>/dev/null | head -1 || echo 'version check skipped')"
else
  echo "[entrypoint] WARN: 未找到 /usr/lib/libreoffice/program/soffice，月报将不可用（其余任务正常）"
fi

exec gunicorn app.web_app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 300
