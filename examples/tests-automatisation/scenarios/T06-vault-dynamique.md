# T06 — Secrets externes et identifiants dynamiques (Vault)

**Objectif** : une connexion **sans mot de passe stocké** : à chaque exécution, un coffre externe fournit des identifiants
**temporaires** pour lire une base. **Impact hors plate-forme** : aucun : le Vault et la base sont des **conteneurs de
développement jetables** (données en mémoire).

## 1. Ressources jetables
```bash
scripts/dev-vault.sh
```
Le script lance un Vault de développement et un PostgreSQL jetable, crée un AppRole et affiche l'adresse, le `role_id` et le `secret_id`.
Il ajoute `127.0.0.1` à `ORCH_EGRESS_ALLOWLIST` dans `.env` : **relancer la pile** (`scripts/dev-down.sh && scripts/dev-up.sh`).
Port de la base jetable : `docker port fluxonata-dev-vault-pg 5432/tcp`.

## 2. Configuration (admin de la plate-forme)
1. Plate-forme → **Coffre externe** : adresse, `role_id`, `secret_id` affichés par le script ; **Tester la connexion**, puis **Activer**.
2. Accès par espace : motifs `secret/shared/*` et `database/creds/*` (lecture seule).
3. Connexions → « Base PostgreSQL » (`base-dynamique`) → étape secret : **Vault → Identifiants dynamiques**, chemin `database/creds/ro`. Hôte `127.0.0.1`, port ci-dessus, base `postgres`. Aucun mot de passe à saisir.

## 3. Prompt
```text
Crée un workflow « Qui suis-je en base ? ».
Aucune information au lancement.
Ce qu'il doit faire :
1. Lire dans la base avec l'étape « Requête SQL » et la connexion « base-dynamique » la requête « SELECT current_user AS utilisateur ». L'étape reste en mode test tant que je ne la passe pas en réel.
2. Produire un rapport « Identité de connexion » avec l'action « Créer un rapport (artefact) » contenant l'utilisateur lu.
Sortie : « utilisateur ».
```

## Vérifications
- [ ] « Tester » la connexion réussit et n'affiche aucun mot de passe.
- [ ] En mode **réel**, `utilisateur` est un nom **généré par Vault** (de la forme `v-approle-ro-…`), différent à chaque exécution après expiration du bail.
- [ ] Deux étapes d'une même exécution réutilisent les mêmes identifiants (un seul bail).
- [ ] Ni le nom généré par Vault, ni un mot de passe n'apparaissent dans l'API des connexions, l'audit ou les journaux (les valeurs sont masquées).
- [ ] **Vault arrêté** (`docker stop fluxonata-dev-vault`) : l'exécution **échoue avec un message explicite** (un message indiquant que le coffre externe est injoignable), sans repli silencieux sur un autre secret ; redémarré, elle réussit de nouveau.
- [ ] Plate-forme → Coffre externe, **Désactiver** : les connexions concernées échouent avec un message qui dit que le coffre est désactivé (toujours sans repli).

## Nettoyage
```bash
scripts/dev-vault.sh --down
```
Supprimer la connexion `base-dynamique`, la configuration du coffre et ses accès, **retirer `127.0.0.1` de `ORCH_EGRESS_ALLOWLIST`**, relancer la pile, déprécier le workflow.
