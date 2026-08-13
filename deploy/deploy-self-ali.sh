#!/usr/bin/env bash
set -Eeuo pipefail

readonly REMOTE_HOST="Self_ALI"
readonly REMOTE_PROJECT="/home/byml/agent/frontier"
readonly SYSTEMD_SERVICE="frontier.service"

if ! command -v ssh >/dev/null 2>&1; then
  echo "错误：本机未安装 ssh。" >&2
  exit 1
fi

echo "正在部署 Frontier 到 ${REMOTE_HOST}:${REMOTE_PROJECT}"
echo "注意：脚本只会部署已经提交并推送到 Git 远端的代码。"

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  "${REMOTE_HOST}" \
  "bash -se -- '${REMOTE_PROJECT}' '${SYSTEMD_SERVICE}'" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

readonly project_dir="$1"
readonly service_name="$2"

failure() {
  local exit_code=$?
  echo "部署失败（远端脚本第 ${BASH_LINENO[0]} 行）。服务尚未完成本次发布。" >&2
  exit "${exit_code}"
}
trap failure ERR

if command -v flock >/dev/null 2>&1; then
  exec 9>"/tmp/frontier-deploy.lock"
  if ! flock -n 9; then
    echo "错误：已有另一个 Frontier 部署任务正在运行。" >&2
    exit 1
  fi
fi

if [[ ! -d "${project_dir}/.git" ]]; then
  echo "错误：${project_dir} 不是 Git 工作区。" >&2
  exit 1
fi

cd "${project_dir}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "错误：服务器工作区存在未提交的受版本控制文件修改，已停止部署：" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

echo "[1/4] 拉取代码"
before_commit="$(git rev-parse --short HEAD)"
git pull --ff-only
after_commit="$(git rev-parse --short HEAD)"
echo "代码版本：${before_commit} -> ${after_commit}"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "错误：服务器未安装 pnpm。" >&2
  exit 1
fi

echo "[2/4] 同步前端依赖"
cd "${project_dir}/web"
pnpm install --frozen-lockfile

echo "[3/4] 构建 Web"
pnpm build

echo "[4/4] 重启 ${service_name}"
if [[ ${EUID} -eq 0 ]]; then
  systemctl restart "${service_name}"
  systemctl is-active --quiet "${service_name}"
else
  if ! sudo -n systemctl restart "${service_name}"; then
    echo "错误：当前 SSH 用户没有非交互 sudo 权限。请按 DEPLOYMENT.md 配置 systemctl NOPASSWD。" >&2
    exit 1
  fi
  sudo -n systemctl is-active --quiet "${service_name}"
fi

cd "${project_dir}"
health_ok=false
for _attempt in {1..15}; do
  if FRONTIER_API_BASE_URL="${FRONTIER_API_BASE_URL:-http://127.0.0.1:8000}" \
    .venv/bin/frontier health >/dev/null 2>&1; then
    health_ok=true
    break
  fi
  sleep 1
done

if [[ ${health_ok} != true ]]; then
  echo "错误：服务已重启，但 15 秒内未通过健康检查。" >&2
  if [[ ${EUID} -eq 0 ]]; then
    systemctl --no-pager --full status "${service_name}" >&2 || true
  else
    sudo -n systemctl --no-pager --full status "${service_name}" >&2 || true
  fi
  exit 1
fi

trap - ERR
echo "部署完成：${service_name} 正常运行，当前版本 ${after_commit}。"
REMOTE_SCRIPT
