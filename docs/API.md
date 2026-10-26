# API

Spécification complète : `docs/openapi.json` (OpenAPI 3, générée par `manage.py spectacular --format openapi-json`, 0 erreur de génération) ; en direct : `GET /api/v1/schema/`.
Les vues sans schéma détaillé y sont documentées comme « objet JSON libre » ; les contrats importants (lancement, brouillon, publication, approbations, déclencheurs) sont typés.

## Conventions
- Base : `/api/v1/`. Toutes les ressources métier sont sous `/workspaces/{espace}/…` ; l'accès est vérifié par espace **et** par droit (un non-membre reçoit `404`).
- Authentification : session + CSRF (navigateur) ou `Authorization: Bearer orch_…` (compte de service ; jeton limité en droits, durée, environnements).
- Pagination : `?limit=&offset=` → `{count, next, previous, results}`.
- Erreurs structurées : `{"error": {"code": "…", "message": "…", "details": …}}` (`400` validation, `401/403` droits, `404`, `409` conflit de révision, `429` limite).
- Limites de débit : `ORCH_API_RATE` (600/min/utilisateur par défaut), `login`, `webhook`, `ai`.
- Idempotence : en-tête `Idempotency-Key` sur les lancements (même clé = même exécution, `200` au lieu de `202`).
- Évolution compatible : champs ajoutés sans retrait dans `v1` ; les workflows publiés figent leurs versions d'actions.

## Lancer un workflow publié (contrat généré)
`GET /workspaces/{espace}/workflows/{slug}/contract/` renvoie le schéma d'entrée, un exemple valide, l'URL et une commande `curl`.
```bash
curl -X POST https://hote/api/v1/workspaces/demo/workflows/analyse-validee/runs/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"environment":"test","inputs":{"text":"un deux trois quatre"}}'
# 202 {"id":"…","status":"running","url":"/api/v1/workspaces/demo/workflow-runs/…/"}
```
Suivi : `GET …/workflow-runs/{id}/` (statut, étapes, erreur et « que faire ensuite ») · `GET …/workflow-runs/{id}/events/?after={seq}` (rattrapage ordonné)
· `GET …/workflow-runs/{id}/artifacts/` · `POST …/workflow-runs/{id}/cancel/` (annulation coopérative puis forcée).
Une version doit être **promue** pour s'exécuter en production (`403` explicite sinon).

## Webhooks entrants
`POST /api/v1/hooks/{id}/` avec `X-Fluxonata-Timestamp` (secondes Unix, ±5 min), `X-Fluxonata-Signature: v1=<hex>` où
`hex = HMAC_SHA256(secret, timestamp + "." + corps_brut)`, et facultativement `X-Fluxonata-Delivery` (identifiant de livraison unique ; sinon la signature sert d'identifiant).
```python
import hmac, hashlib, time, json, urllib.request
body = json.dumps({"text": "bonjour"}).encode(); ts = str(int(time.time()))
sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(url, data=body, headers={"X-Fluxonata-Timestamp": ts, "X-Fluxonata-Signature": f"v1={sig}", "Content-Type": "application/json"})
```
Toute cause d'échec renvoie `401` (aucun oracle) ; un rejeu renvoie `200 {"status":"duplicate"}` sans relancer.

## Rappels sortants
`callback_url` (facultatif) au lancement : à la fin du run, un `POST` JSON minimal (`run_id`, `status`, `workflow`, `version`, `url` — **sans sorties**) est envoyé depuis une outbox
transactionnelle, avec reprises exponentielles (6 essais), signature HMAC si un secret `callback-signing` existe, et contrôle SSRF à l'enregistrement **et** à l'envoi.
