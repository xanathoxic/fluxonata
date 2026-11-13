#!/usr/bin/env bash
# Browser E2E + axe accessibility checks against the running dev stack (run scripts/dev-up.sh first).
# If chromium system libs are missing and you have no root, they are extracted to .run/libs (no system change):
#   apt-get download libnspr4 libnss3 libasound2t64 ; dpkg -x each .deb into .run/libs/root
# Données : chaque scénario crée ses objets avec un identifiant unique (workflows, formulaires, espaces, exécutions), sans nettoyage.
# Sur une base neuve (CI, pile jetable) la suite est reproductible. Relancée sur la même base, les noms uniques évitent les collisions,
# mais les objets s'accumulent (espaces, exécutions, listes plus longues) : cela peut changer les comptes, la pagination ou les listes
# lues par un autre scénario. Pour un résultat comparable, utiliser une base jetable ; les scénarios qui lisent des listes
# (exécutions, utilisateurs) créent eux-mêmes ce qu'ils comptent.
source "$(dirname "$0")/_common.sh"
# Avertissement (non bloquant) : une machine saturée (swap presque plein, forte pression mémoire) fige les serveurs pendant des
# dizaines de secondes ; des délais d'attente E2E y échouent sans lien avec le code (constaté : API muette 19 s, swap à 99 %).
if [ -r /proc/meminfo ]; then
  awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{ if (t>0 && (t-f)*100/t>90) printf "ATTENTION : swap utilisé à %d %% ; la machine est saturée, les résultats E2E peuvent être faussés (délais).\n", (t-f)*100/t > "/dev/stderr" }' /proc/meminfo
  [ -r /proc/pressure/memory ] && awk -F'[ =]' '/^some/{ if ($5+0>10) printf "ATTENTION : pression mémoire élevée (avg60=%s %%) ; les résultats E2E peuvent être faussés (délais).\n", $5 > "/dev/stderr" }' /proc/pressure/memory
fi
lib="$ROOT/.run/libs/root/usr/lib/x86_64-linux-gnu"
[ -d "$lib" ] && export LD_LIBRARY_PATH="$lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Les tests s'appuient sur les comptes de démo documentés (alice concepteur, etc.) : on les remet en l'état avant de lancer.
load_env; (cd "$ROOT/backend" && "$ROOT/.venv/bin/python" manage.py seed_demo --reset-accounts | tail -1)
cd "$ROOT/frontend" && npx playwright install chromium >/dev/null && exec npx playwright test "$@"
