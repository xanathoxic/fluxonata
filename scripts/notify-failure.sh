#!/usr/bin/env bash
# Prévient d'un échec d'une tâche planifiée (sauvegarde, essai de restauration) : une ligne au journal, et, SI une destination est
# configurée, un message JSON {"text": …} envoyé à cette adresse (chat, supervision : l'adresse est son propre secret, jamais écrite
# dans le journal). Sans destination, seule la ligne du journal signale l'échec. Aucun contenu de base ni de sauvegarde n'est envoyé.
# Usage : scripts/notify-failure.sh <nom de l'unité>        Destination : variable FLUXONATA_NOTIFY_URL (http ou https)
# Branchement : infra/systemd/fluxonata-notify@.service lit /etc/fluxonata/notify.env (droits 600, hors dépôt) ; rien n'est activé ici.
set -euo pipefail
UNIT="${1:?usage : notify-failure.sh <unité>}"
MSG="Fluxonata : l'unité ${UNIT} a échoué sur $(hostname) ($(date -u +%FT%TZ))."
echo "$MSG" >&2
URL="${FLUXONATA_NOTIFY_URL:-}"
if [ -z "$URL" ]; then
  echo "Aucune destination (FLUXONATA_NOTIFY_URL) : seule cette ligne du journal signale l'échec." >&2
  exit 0
fi
case "$URL" in
  http://* | https://*) ;;
  *) echo "Destination refusée : une adresse http:// ou https:// est attendue." >&2; exit 2 ;;
esac
PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$MSG")"
if ! curl -sS --max-time 15 -H 'Content-Type: application/json' -d "$PAYLOAD" -o /dev/null --fail "$URL" 2>/dev/null; then
  echo "Envoi impossible : destination injoignable ou refusée (l'adresse n'est pas journalisée)." >&2
  exit 1
fi
