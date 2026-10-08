#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "用法：$0 ENV_FILE ENV_EXAMPLE MANAGED_KEYS" >&2
  exit 2
fi

readonly env_file="$1"
readonly example_file="$2"
readonly managed_keys_file="$3"

for required_file in "${env_file}" "${example_file}" "${managed_keys_file}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "错误：环境同步所需文件不存在：${required_file}" >&2
    exit 1
  fi
done

env_dir="$(dirname "${env_file}")"
env_name="$(basename "${env_file}")"
temporary_file="$(mktemp "${env_dir}/.${env_name}.deploy.XXXXXX")"
cleanup() {
  rm -f -- "${temporary_file}"
}
trap cleanup EXIT

cp -p -- "${env_file}" "${temporary_file}"
declare -a additions=()

while IFS= read -r raw_key || [[ -n "${raw_key}" ]]; do
  key="${raw_key%$'\r'}"
  if [[ -z "${key}" || "${key}" == \#* ]]; then
    continue
  fi
  if [[ ! "${key}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
    echo "错误：受控环境变量列表中存在非法名称：${key}" >&2
    exit 1
  fi
  if grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "${env_file}"; then
    continue
  fi

  example_line=""
  example_line_count=0
  while IFS= read -r candidate; do
    example_line="${candidate}"
    example_line_count=$((example_line_count + 1))
  done < <(grep -E "^${key}=" "${example_file}" || true)
  if [[ ${example_line_count} -ne 1 ]]; then
    echo "错误：${example_file} 必须且只能定义一次 ${key}。" >&2
    exit 1
  fi
  additions+=("${example_line}")
done < "${managed_keys_file}"

if [[ ${#additions[@]} -eq 0 ]]; then
  echo "运行环境：受控配置已是最新，无需修改。"
  exit 0
fi

{
  printf '\n# Added automatically by Frontier deployment; existing values are never replaced.\n'
  printf '%s\n' "${additions[@]}"
} >> "${temporary_file}"
mv -f -- "${temporary_file}" "${env_file}"
trap - EXIT

echo "运行环境：已补充 ${#additions[@]} 个缺失配置："
for addition in "${additions[@]}"; do
  echo "  - ${addition%%=*}"
done
