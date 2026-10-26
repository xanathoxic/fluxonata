#!/usr/bin/env bash
# Print the immutable reference (repo@digest) of the runtime images currently pulled, to pin them in production.
source "$(dirname "$0")/_common.sh"; load_env
for img in "${ORCH_PYTHON_IMAGE:-python:3.13-slim}" "${ORCH_NODE_IMAGE:-node:22-slim}" "${ORCH_POWERSHELL_IMAGE:-mcr.microsoft.com/powershell:lts-debian-12}" postgres:17-alpine; do
  d="$(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null || true)"
  printf '%-55s %s\n' "$img" "${d:-(non téléchargée)}"
done
