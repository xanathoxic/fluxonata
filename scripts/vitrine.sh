#!/usr/bin/env bash
# Crée l'espace « Vitrine » (scénario d'exploitation complet : skills IA, workflows imbriqués, boucles, approbation…)
# et le relie à un serveur d'IA local compatible OpenAI (Ollama par défaut). Développement uniquement.
#   scripts/vitrine.sh [modèle]            ex. scripts/vitrine.sh qwen3-coder:30b
#   OLLAMA_URL=http://hote:11434 scripts/vitrine.sh
# Sous WSL, un Ollama installé sous Windows est joignable par l'adresse de la passerelle (détectée ici) ; il doit écouter
# au-delà de localhost (OLLAMA_HOST=0.0.0.0 côté Windows). Par sécurité (SSRF), la plate-forme refuse les adresses privées
# sauf si l'hôte est autorisé explicitement : ce script l'ajoute à ORCH_EGRESS_ALLOWLIST dans .env (non versionné).
source "$(dirname "$0")/_common.sh"
cd "$ROOT"
MODEL="${1:-qwen3-coder:30b}"
URL="${OLLAMA_URL:-}"
if [ -z "$URL" ]; then
  GW="$(ip route 2>/dev/null | awk '/default/ {print $3; exit}')"
  for h in 127.0.0.1 $GW; do
    if curl -s -m 3 -o /dev/null "http://$h:11434/api/tags"; then URL="http://$h:11434"; break; fi
  done
fi
[ -f .env ] || { echo "→ .env absent : lancez d'abord scripts/dev-up.sh"; exit 1; }
if [ -z "$URL" ]; then
  echo "⚠ Aucun serveur d'IA détecté (Ollama sur le port 11434) : espace créé avec un faux fournisseur, les étapes IA ne répondront pas."
  ARGS=(--fake-ai)
else
  HOST="$(echo "$URL" | sed -E 's#^https?://([^:/]+).*#\1#')"
  echo "→ serveur d'IA : $URL (modèle $MODEL)"
  CUR="$(grep -E '^ORCH_EGRESS_ALLOWLIST=' .env | cut -d= -f2- || true)"
  if ! echo ",$CUR," | grep -q ",$HOST,"; then
    NEW="${CUR:+$CUR,}$HOST"
    if grep -q '^ORCH_EGRESS_ALLOWLIST=' .env; then sed -i "s|^ORCH_EGRESS_ALLOWLIST=.*|ORCH_EGRESS_ALLOWLIST=$NEW|" .env; else echo "ORCH_EGRESS_ALLOWLIST=$NEW" >> .env; fi
    echo "→ hôte autorisé dans .env (ORCH_EGRESS_ALLOWLIST=$NEW) : redémarrage de la pile"
    "$ROOT/scripts/dev-down.sh" >/dev/null 2>&1 || true
    "$ROOT/scripts/dev-up.sh" >/dev/null
    sleep 5
  fi
  ARGS=(--ollama-url "$URL" --model "$MODEL")
fi
load_env
(cd backend && "$ROOT/.venv/bin/python" manage.py seed_showcase --reset "${ARGS[@]}")
echo "→ Ouvrez http://127.0.0.1:${ORCH_WEB_PORT:-5173}, choisissez l'espace « Vitrine — Exploitation » (alice / demo-fluxonata), puis"
echo "  Workflows → « Gestion d'un incident d'infrastructure » → Lancer. L'approbation se décide avec claire / demo-fluxonata."
