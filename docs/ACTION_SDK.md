# Écrire une action (SDK d'actions)

Une **action** est une brique exécutable : un petit programme qui reçoit un objet JSON et renvoie un objet JSON.
Elle s'exécute toujours dans un **conteneur jetable** (jamais dans le processus web), sans réseau par défaut.

## Contrat
| Élément | Règle |
|---|---|
| Entrée | objet JSON validé par `input_schema` (JSON Schema, `type: object`) avant le lancement |
| Sortie | objet JSON, validé par `output_schema` après l'exécution ; sinon l'étape échoue (`OUTPUT_INVALID`) |
| Journaux | tout ce que le code écrit sur `stderr` (Python/Node : aussi `print`/`console.log`) ; jamais mélangés au résultat |
| Erreur | lever une exception / sortir avec un code ≠ 0 : le message devient l'erreur affichée à l'utilisateur |
| Version | semver ; une version publiée est **immuable** ; dépendances verrouillées par l'image d'exécution |
| Effets | `side_effects: none | external` et `idempotent: true | false` — décrivez-les honnêtement |
| Réseau | `none` (défaut) ou `test-service` (service local de démonstration) ; aucun accès Internet |
| Limites | `timeout_seconds` (1–3600) et `memory_mb` (16–2048) |
| Secrets | déclarer `secrets_required: [{"slot":"api","env":"API_TOKEN"}]` ; la valeur est injectée en variable d'environnement **uniquement** si le workflow relie une connexion à ce « slot » |
| Artefacts | renvoyer `"__artifacts__": [{"name":"rapport.md","content_type":"text/markdown","text":"…"}]` (max 10 fichiers, 5 Mo) |

### Idempotence et effets externes (important)
La livraison est **au moins une fois**. Si le worker disparaît pendant une action `external` **non idempotente**, la plate-forme ne la relance
**pas** : l'étape passe en « issue incertaine » et un humain décide (réussie / à relancer / abandonnée). Pour que vos actions puissent être
relancées sans risque, rendez-les idempotentes (clé d'idempotence côté système cible) et déclarez `idempotent: true`.

## Langages
| Runtime | Fichier | Entrée | Sortie |
|---|---|---|---|
| `python` | `action.py` | fonction `run(inputs) -> dict` | valeur de retour |
| `node` | `action.mjs` | `export function run(inputs)` (async possible) | valeur de retour |
| `typescript` | `action.ts` | idem (types retirés par Node ; pas de vérification de types à l'exécution) | idem |
| `shell` | `action.sh` | JSON dans le fichier `$ORCH_INPUT_FILE` (clé `inputs`) | **dernière ligne de stdout = JSON** ; stderr = journaux |
| `powershell` | `action.ps1` | idem shell | idem shell (image PowerShell dédiée) |

Protocole v1 (Python/Node) : le harnais lit `input.json`, appelle le point d'entrée et écrit **une** enveloppe sur la vraie sortie standard :
`{"protocol":1,"ok":true,"result":{…}}` ou `{"protocol":1,"ok":false,"error":{"code":"…","message":"…"}}`.

## Exemple Python
```python
def run(inputs):
    words = inputs["text"].split()
    print("analyse de", len(words), "mots")          # -> journaux
    return {"words": len(words)}
```
Schéma d'entrée : `{"type":"object","required":["text"],"properties":{"text":{"type":"string","title":"Texte","maxLength":10000}}}`

## Publier
Interface : *Bibliothèques → Actions → Nouvelle action*. API : `POST /api/v1/workspaces/{espace}/library/actions/`.
Le serveur vérifie la syntaxe (Python), la validité des schémas, l'absence de secret dans le code, les limites et la politique réseau.
**Il n'exécute jamais le code à la publication ni à l'import.**

## Tester
Lancez l'action depuis le catalogue avec un jeu d'essai ; l'assistant IA (s'il est activé) peut proposer des jeux de test valides.
Tests automatisés de référence : `backend/tests/test_runtimes.py`, `backend/tests/test_worker.py`.

## Tâches script (code écrit dans le workflow)
Sans créer d'action, une étape « Tâche script » (Python ou JavaScript) s'exécute dans le **même bac à sable** que les actions (conteneur jetable, sans réseau,
uid 65534, rootfs en lecture seule, limites, délai 1–600 s). Les entrées liées deviennent des variables ; `inputs` et `vars` sont disponibles ; `System.log/warn/error`
écrivent dans le journal (stderr). Les sorties déclarées (nom + type) doivent exister à la fin du script, sinon l'étape échoue avec un message qui n'écrit jamais les valeurs.
Les secrets ne sont jamais accessibles : utilisez une action avec connexion. Le test depuis l'éditeur utilise `POST …/workflows/<slug>/test-script/`.
