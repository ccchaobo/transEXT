#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
action="${1:-start}"
case "$action" in start|stop|status|logs|backup|update) ;; *) echo '用法: bash deploy.sh [start|stop|status|logs|backup|update]' >&2; exit 2;; esac
command -v docker >/dev/null || { echo '请先安装并启动 Docker Engine/Docker Desktop。' >&2; exit 1; }
docker info >/dev/null || { echo 'Docker 引擎未就绪，请启动 Linux 容器引擎。' >&2; exit 1; }
docker compose version >/dev/null
if [[ ! -f .env.docker ]]; then cp deploy/docker.env.example .env.docker; chmod 600 .env.docker; fi
dc() { docker compose --env-file .env.docker -f compose.yaml "$@"; }
dc config --quiet
backup() {
  mkdir -p backups
  local folder stamp
  folder="$(cd backups && pwd)"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dc stop worker api
  local result=0
  # Always resume the original services, including after an archive failure.
  dc run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE -v "$folder:/backup" api \
    python deploy/archive.py create /data "/backup/reader-$stamp.tar.gz" --owner "$(id -u):$(id -g)" || result=$?
  dc up -d --wait --wait-timeout 360 api worker
  return "$result"
}
case "$action" in
  start) dc up -d --build --wait --wait-timeout 360; dc ps; dc port api 8765 ;;
  stop) dc down ;;
  status) dc ps ;;
  logs) dc logs --tail=150 -f ;;
  backup) backup ;;
  update) backup; dc up -d --build --wait --wait-timeout 360 ;;
esac
if [[ "$action" == start || "$action" == update ]]; then
  echo '部署完成。打开上面 api 映射的本机端口，在“翻译设置”中配置服务。'
fi
