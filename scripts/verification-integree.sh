#!/usr/bin/env bash
# Vérification INTÉGRÉE : toute la chaîne de contrôle (`scripts/ci.sh` : format, lint, typage, migrations, pytest, tsc + build, linter
# frontend, vitest avec couverture, audits, secrets, SBOM, règles d'alerte, E2E + axe), journalisée avec le commit vérifié.
# À lancer sur une machine qui a les ressources (mémoire, processeurs, Docker) ou en CI : elle REFUSE de démarrer sur une machine
# saturée, car des délais d'attente y échouent sans lien avec le code (voir le suivi de conformité, lot 11).
# ATTENTION : cette variante agit sur l'arbre de travail et sur la pile de développement ; pour valider un commit exact sans toucher
# à la pile, à sa base ni aux comptes de démonstration, utiliser scripts/verification-isolee.sh.
# Prérequis : pile de développement lancée (`scripts/dev-up.sh`) pour l'étape E2E ; elle remet les comptes de démonstration à zéro.
# Usage : scripts/verification-integree.sh [--force] [--skip-e2e]     (--force : outrepasser le refus ; à éviter)
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
FORCE=0; ARGS=()
for a in "$@"; do [ "$a" = "--force" ] && FORCE=1 || ARGS+=("$a"); done
problems=()
if [ -r /proc/meminfo ]; then
  swap_pct="$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{ if (t>0) printf "%d", (t-f)*100/t; else print 0 }' /proc/meminfo)"
  avail_mb="$(awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo)"
  [ "$swap_pct" -gt 50 ] && problems+=("swap utilisé à ${swap_pct} % (seuil 50 %)")
  [ "$avail_mb" -lt 4096 ] && problems+=("mémoire disponible ${avail_mb} Mo (minimum 4096 Mo)")
fi
if [ -r /proc/pressure/memory ]; then
  psi="$(awk -F'[ =]' '/^some/{print int($5)}' /proc/pressure/memory)"
  [ "$psi" -gt 5 ] && problems+=("pression mémoire ${psi} % sur 60 s (seuil 5 %)")
fi
command -v docker >/dev/null || problems+=("Docker introuvable")
if [ "${#problems[@]}" -gt 0 ]; then
  printf 'MACHINE NON ADAPTÉE À LA VÉRIFICATION INTÉGRÉE :\n'; printf '  - %s\n' "${problems[@]}"
  if [ "$FORCE" = 0 ]; then echo "Refus (aucune étape lancée). Libérez des ressources ou lancez en CI ; --force outrepasse ce refus." >&2; exit 3; fi
  echo "ATTENTION : --force, résultats à interpréter avec prudence." >&2
fi
LOG="$ROOT/artifacts/verification-integree-$(date -u +%Y%m%dT%H%M%SZ).log"; mkdir -p "$ROOT/artifacts"
{ echo "commit : $(git rev-parse --short HEAD) $(git diff --quiet && echo 'propre' || echo 'MODIFICATIONS NON VALIDÉES')"; echo "début  : $(date -u +%FT%TZ)"; } | tee "$LOG"
set +e; scripts/ci.sh "${ARGS[@]}" 2>&1 | tee -a "$LOG"; rc=${PIPESTATUS[0]}; set -e
echo "fin    : $(date -u +%FT%TZ) ; code de sortie de ci.sh : $rc" | tee -a "$LOG"
echo "Journal : $LOG (à joindre au suivi avec le commit)"; exit "$rc"
