# Exemples du nœud « Commandes SSH »

Trois workflows complets, **sans secret et sans équipement réel** (mode test par défaut : aucune connexion n'est ouverte).

| Fichier | Montre |
|---|---|
| [`exemple-ssh-diagnostic.json`](exemple-ssh-diagnostic.json) | commandes indépendantes (`exec`) issues d'une entrée, un rebond, branche d'erreur qui lit les résultats partiels |
| [`tester-connexion-ssh-simulee.json`](tester-connexion-ssh-simulee.json) | démonstration **entièrement simulée** (réponses factices pour des chaînes `enable` et `show`), résumé et diagnostic sans effet externe ; connexion attendue : `fluxonata_client_key` (type SSH) |
| [`exemple-ssh-session.json`](exemple-ssh-session.json) | session interactive (`shell`) avec PTY, attente de prompt, motif d'échec, réponse à une demande de mot de passe par **référence** à un secret |

## Avant de les importer
1. Menu **Connexions** : créez deux connexions de type « Serveur SSH » nommées `bastion-exemple` et `cible-exemple` (hôte, port, identifiant, **empreinte de la clé du serveur** `SHA256:…`, mot de passe ou clé privée comme secret). L'empreinte est obligatoire : une clé inconnue ou différente est refusée.
2. Pour la session interactive, créez aussi un secret nommé `secret-exemple` (le mot de passe à fournir quand le serveur le demande).
3. Importez les fichiers comme brouillons : `scripts/workflows_sync.py import --url … --workspace … --from examples/ssh/` (voir `docs/WORKFLOWS_AS_CODE.md`).
4. Lancez en mode **test** : les résultats sont simulés (`simulated: true`) à partir du champ `mocks`. Passez en mode **réel** seulement après relecture, et adaptez les motifs de la session (`expect`, `failure`) à votre serveur : la plateforme n'en connaît aucun.

**Une clé pour plusieurs serveurs** : créez une connexion SSH **sans serveur** (identifiant + clé privée) et indiquez, dans l'étape, `host`, `port` et `host_key_sha256` de chaque serveur (cible et rebonds `{connection, host, host_key_sha256}`) ; voir « Commandes SSH » dans `docs/USER_GUIDE.md`.

Les commandes et les motifs sont ceux du workflow : rien n'est propre à un équipement. Voir « Commandes SSH » dans `docs/USER_GUIDE.md`.
