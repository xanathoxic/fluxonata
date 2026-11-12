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
lib="$ROOT/.run/libs/root/usr/lib/x86_64-linux-gnu"
[ -d "$lib" ] && export LD_LIBRARY_PATH="$lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Les tests s'appuient sur les comptes de démo documentés (alice concepteur, etc.) : on les remet en l'état avant de lancer.
load_env; (cd "$ROOT/backend" && "$ROOT/.venv/bin/python" manage.py seed_demo --reset-accounts | tail -1)
cd "$ROOT/frontend" && npx playwright install chromium >/dev/null && exec npx playwright test "$@"
