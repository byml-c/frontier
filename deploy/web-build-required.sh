#!/usr/bin/env bash
set -Eeuo pipefail

cd "$1"
revision_file="web/dist/.frontier-build-revision"

# Only reuse a complete, successfully recorded build of the current frontend.
# A missing/invalid marker rebuilds conservatively, including failed build retries.
if [[ -s web/dist/index.html && -s "${revision_file}" ]]; then
  built_revision="$(cat "${revision_file}")"
  if [[ "${built_revision}" =~ ^[0-9a-f]{40}$ ]] \
    && git cat-file -e "${built_revision}^{commit}" 2>/dev/null \
    && git diff --quiet "${built_revision}" HEAD -- web; then
    echo reuse
    exit 0
  fi
fi

echo build
