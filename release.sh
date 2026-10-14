#!/usr/bin/env bash
# ============================================================
# 政企 AI 工作流平台 · 一键发版脚本
# ------------------------------------------------------------
# 作用：把本月报修复（LIBREOFFICE_URL 远端重算）推到 GitLab 部署源，
#       并重建容器，使线上 winsun.net.cn 月报恢复可用。
# 适用：在能访问内网 GitLab + 部署服务器的终端运行
#       （本机 Mac 的钥匙串含 GitLab 凭据，会自动填充，无需手输）。
#
# 用法：
#   1) 仅推送 GitLab（若 Jenkins 已配 GitLab webhook，推送即触发自动构建）：
#        ./release.sh
#   2) 推送 + SSH 到部署服务器自动重建：
#        DEPLOY_HOST=user@172.18.101.x ./release.sh
#      （COMPOSE_DIR 默认 /opt/zhengqi-ai-workflow-platform，可按需覆盖）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

GITLAB_REMOTE="${GITLAB_REMOTE:-gitlab}"
DEPLOY_HOST="${DEPLOY_HOST:-}"                       # 例： root@172.18.101.180
COMPOSE_DIR="${COMPOSE_DIR:-/opt/zhengqi-ai-workflow-platform}"

echo "============================================================"
echo " 政企 AI 工作流平台 · 发版"
echo "   GitLab 远端 : $GITLAB_REMOTE"
echo "   部署主机   : ${DEPLOY_HOST:-<未设置，仅推送>}"
echo "============================================================"

# ---------- 1. 推送 GitLab 部署源 ----------
echo "[1/3] 推送 $GITLAB_REMOTE/main ..."
git push "$GITLAB_REMOTE" main

if [ -z "$DEPLOY_HOST" ]; then
  echo
  echo "[2/3] 未设置 DEPLOY_HOST："
  echo "   - 若 Jenkins 已配置 GitLab webhook → 推送已触发自动构建，等待流水线完成即可。"
  echo "   - 若未配置 webhook → 请到 Jenkins 手动触发一次构建（用仓库内 jenkins_build.sh）。"
  echo "   或设置 DEPLOY_HOST 后重跑本脚本以 SSH 直连重建："
  echo "       DEPLOY_HOST=user@host ./release.sh"
  exit 0
fi

# ---------- 2. SSH 到部署服务器重建 ----------
echo "[2/3] SSH 到 $DEPLOY_HOST 拉取并重建 ..."
ssh "$DEPLOY_HOST" bash -s <<EOF
  set -euo pipefail
  cd "$COMPOSE_DIR"
  git pull "$GITLAB_REMOTE" main
  # 容器内 LibreOffice 已不再使用（改走 LIBREOFFICE_URL 远端服务），
  # 但为稳妥保留镜像内 soffice；重建仅需让 monthly_4plus2.py 改动进镜像。
  if [ -f jenkins_build.sh ]; then
    bash jenkins_build.sh
  else
    docker compose build && docker compose up -d
  fi
EOF

# ---------- 3. 校验 ----------
echo "[3/3] 完成。请访问以下地址验证月报是否恢复："
echo "   https://zhengqi-ai-workflow-platform.winsun.net.cn/task/monthly_4plus2"
echo "   若仍异常，在服务器执行： docker logs zhengqi-ai-workflow --tail 100"
