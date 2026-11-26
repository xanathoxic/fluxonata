#!/usr/bin/env bash
# Construit l'image du runtime « ansible » (infra/Dockerfile.runner-ansible), par défaut sous le nom attendu par ORCH_ANSIBLE_IMAGE.
set -euo pipefail
cd "$(dirname "$0")/.."
tag="${1:-fluxonata-runner-ansible:local}"
docker build -f infra/Dockerfile.runner-ansible -t "$tag" infra
echo "Image construite : $tag"
