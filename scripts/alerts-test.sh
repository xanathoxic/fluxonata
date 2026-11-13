#!/usr/bin/env bash
# Vérifie les règles d'alerte Prometheus (infra/monitoring) : syntaxe (promtool check rules) puis déclenchement et non-déclenchement
# sur des séries SYNTHÉTIQUES (promtool test rules). Rien n'est scruté, aucun service n'est lancé, aucune notification n'est envoyée.
# Image promtool épinglée par condensat (réseau : téléchargement de l'image la première fois). Usage : scripts/alerts-test.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="prom/prometheus:v3.5.0@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996"
run() { docker run --rm --network none -v "$ROOT/infra/monitoring:/m:ro" -w /m --entrypoint promtool "$IMAGE" "$@"; }
echo "== promtool check rules"; run check rules fluxonata.rules.yml
echo "== promtool test rules"; run test rules fluxonata.rules.test.yml
