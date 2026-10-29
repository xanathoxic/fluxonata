#!/usr/bin/env python3
"""Appelle un webhook entrant de Fluxonata avec la signature attendue (voir docs/API.md, « Webhooks entrants »).

Sert à simuler l'arrivée d'un e-mail ou d'une alerte dans les tests d'automatisation, sans service externe.
Le secret du webhook se passe par la variable d'environnement FLUXONATA_WEBHOOK_SECRET (jamais en argument : il finirait dans l'historique du shell).

  export FLUXONATA_WEBHOOK_SECRET='…'      # affiché une seule fois à la création du webhook
  python3 envoyer_webhook.py http://127.0.0.1:8100/api/v1/hooks/<id>/ '{"objet": "Test"}'
  python3 envoyer_webhook.py <url> @courriel.json
"""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 2
    url, raw = argv[1], argv[2]
    secret = os.environ.get("FLUXONATA_WEBHOOK_SECRET", "")
    if not secret:
        print("Définissez FLUXONATA_WEBHOOK_SECRET (le secret du webhook).", file=sys.stderr)
        return 2
    if raw.startswith("@"):
        with open(raw[1:], "rb") as fh:
            body = fh.read()
    else:
        body = raw.encode()
    json.loads(body)  # échoue tôt si le corps n'est pas du JSON
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-Fluxonata-Timestamp": ts, "X-Fluxonata-Signature": f"v1={sig}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 - l'adresse est celle que vous donnez
            print(resp.status, resp.read().decode()[:500])
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode()[:500])
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
