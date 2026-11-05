# Administration

## Rôles et droits
| Rôle | Consulter | Modifier | Publier | Lancer | Approuver | Connexions | Administrer |
|---|---|---|---|---|---|---|---|
| Administrateur / Responsable d'espace | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Concepteur | ✔ | ✔ | ✔ | ✔ | — | ✔ | — |
| Opérateur | ✔ | — | — | ✔ | — | — | — |
| Approbateur | ✔ | — | — | — | ✔ | — | — |
| Lecteur | ✔ | — | — | — | — | — | — |

Les droits sont vérifiés **côté serveur** à chaque requête ; l'interface ne fait que masquer des boutons. Un utilisateur qui n'appartient pas à un espace reçoit
un `404` (l'existence des objets n'est pas révélée). `tests/test_isolation_sweep.py` parcourt automatiquement toutes les routes d'espace.

## Administrateurs de la plate-forme
Deux niveaux distincts :
- **Administrateur d'espace** (rôle ci-dessus) : gère *son* espace — membres, jetons, politiques, audit, santé de l'espace.
- **Administrateur de la plate-forme** (drapeau `is_superuser` du compte) : crée et administre les **espaces**, voit les **processus partagés** (workers, orchestrateur) et gère les autres administrateurs de la plate-forme. Menu « Plate-forme ».

Principes (testés dans `tests/test_platform.py`) :
- **Aucun accès implicite aux données** : être administrateur de la plate-forme ne donne accès à *aucun* espace (réponse `404`). Pour intervenir, il faut être ajouté comme membre — action explicite, journalisée (« Ajouter un administrateur » sur l'espace, ou à la création).
- Jamais un compte de service ni un jeton d'API (`403`).
- Au moins un administrateur de la plate-forme reste toujours (refus de retirer le dernier, dans l'interface comme en ligne de commande).
- Tout est audité (`platform.*`) dans un journal propre à la plate-forme, invisible des espaces.

Écrans : **Espaces** (créer — environnements dev/recette/production créés automatiquement —, renommer, ajouter un administrateur, archiver/restaurer), **Processus** (état des workers et de l'orchestrateur, « oublier » les processus arrêtés), **Administrateurs**, **Journal**.
**Archiver un espace** le rend inaccessible (`404`, il disparaît du sélecteur), désactive ses planifications et refuse ses webhooks ; rien n'est supprimé, les exécutions déjà en cours se terminent, la restauration réactive l'accès (les planifications restent désactivées : à réactiver volontairement).

Ligne de commande (premier démarrage, dépannage) :
```bash
python manage.py bootstrap --workspace principal --admin-user admin   # le premier administrateur est aussi administrateur de la plate-forme (--no-platform-admin pour l'éviter)
python manage.py platform_admin alice            # accorder
python manage.py platform_admin alice --revoke   # retirer (refusé s'il est le dernier)
```
**Workers** : ce sont des processus système. Les démarrer, les arrêter ou en ajouter relève de l'exploitation (`docker compose up --scale worker=N`, systemd) ; l'interface ne fait que les *observer*. Il n'existe pas (encore) de pools de workers ni de quotas par espace : tous les espaces partagent la même flotte.

## Utilisateurs (administrateurs de la plate-forme)
Plate-forme → **Utilisateurs** : liste (recherche par identifiant ou e-mail) avec état, espaces et rôles, dernière connexion.
- **Créer** un compte local : identifiant (non modifiable), e-mail facultatif, **espaces et rôles** (un ou plusieurs, un rôle par espace). Sans mot de passe saisi, un **mot de passe provisoire** est généré, **affiché une seule fois** ; la personne doit en choisir un nouveau à sa première connexion (tant que ce n'est pas fait, seule cette route répond, **côté serveur**).
- **Espaces et rôles** : ajouter la personne à un autre espace, changer son rôle, la retirer — appliqué immédiatement, journalisé aussi dans le journal de l'espace (`member.add|role|remove`, `via: platform`). Un espace garde toujours au moins un responsable.
- **Modifier** : e-mail. **Réinitialiser le mot de passe** : l'ancien cesse de marcher, nouveau mot de passe provisoire (comptes SSO : non applicable, le mot de passe est chez le fournisseur d'identité).
- **Désactiver / réactiver** : un compte désactivé ne peut plus se connecter et ses sessions ouvertes s'arrêtent aussitôt ; ses appartenances, ses exécutions, ses approbations et son nom dans le journal sont conservés. **Il n'y a pas de suppression de compte** (l'historique perdrait son auteur). Garde-fous : on ne se désactive pas soi-même, on ne désactive pas le dernier administrateur de la plate-forme actif.
- Mots de passe choisis : 12 caractères au moins, pas un mot de passe courant ni uniquement des chiffres, pas proche de l'identifiant. Tout le monde change le sien dans **Mon compte** (clic sur son nom dans l'en-tête) ; les autres sessions sont alors fermées.
- Les **comptes de service** ne sont pas des personnes : ils se gèrent dans leur espace (Jetons et comptes de service). Les comptes **SSO** sont créés par le fournisseur d'identité à la première connexion ; on peut ensuite les placer dans des espaces.
- Tout est audité (`platform.user_create|update|deactivate|reactivate|reset_password|membership`, `auth.password_change`) ; aucun mot de passe, provisoire ou non, n'est écrit dans le journal.
- Un administrateur de **l'espace** peut toujours ajouter à son espace un compte existant (Administration → Membres) ; créer le compte reste réservé à la plate-forme.

## Écrans (Administration d'un espace)
- **Santé** : base, orchestrateur, workers (nombre), file d'attente, exécutions orphelines, effets incertains, rappels en échec — avec la marche à suivre. Le détail des processus est réservé à la plate-forme.
- **Membres** : ajout d'un compte existant, changement de rôle, retrait (un espace garde toujours au moins un responsable).
- **Jetons et comptes de service** : identités non humaines ; jetons limités en droits (consulter/lancer/modifier/publier — jamais approuver ni administrer),
  en durée (1–365 jours) et en environnements ; le jeton n'est affiché qu'une fois, seul son hachage SHA-256 est conservé.
- **Politiques** : versionnées ; chaque exécution conserve la version qui l'a gouvernée.
  `prod_requires_promotion`, `promotion_requires_review`, `forbid_self_review`, `forbid_self_approval_in_prod`, `require_signed_packages`.
- **Runners** (dédiés à l'espace) : agents installés sur vos machines qui exécutent les actions de l'espace, et case « Autoriser les runners partagés » pour réserver l'exécution à vos runners. Voir `docs/RUNNERS.md`. La plate-forme gère en plus tous les runners (onglet Runners de Plate-forme).
- **Audit** : filtre par type, export NDJSON. Append-only **applicatif** : un administrateur de base de données peut le modifier ; exportez vers un stockage indépendant
  si une exigence de preuve l'impose.
- **Assistant IA** : désactivé par défaut (voir ci-dessous). Les **skills IA** de la bibliothèque (Bibliothèques → Skills IA) ne sont que de la configuration versionnée ; ils s'exécutent uniquement via une étape IA, sous la politique de l'espace.

## Promotion en production
Une version de workflow doit être **promue** avant de s'exécuter en production : demande par un concepteur, revue par une **autre** personne ayant le droit d'approuver
(configurable). Les exécutions planifiées et les webhooks suivent la même règle. Les approbations humaines en production ne peuvent pas être auto-approuvées.

## Connexions et secrets
*Connexions* : assistant en 4 étapes. Le secret est chiffré (Fernet, clés hors base et hors dépôt, rotation : `manage.py rotate_keys`), n'est jamais réaffiché,
n'est injecté (variable d'environnement via un fichier privé) que dans l'action qui déclare le slot correspondant, et est masqué dans journaux et résultats
(défense complémentaire : l'egress réseau coupé reste le vrai garde-fou). Chaque injection est auditée.

### Coffre externe (HashiCorp Vault)
Un administrateur de la plate-forme déclare **un** Vault (Plate-forme → *Coffre externe*) : adresse (https), AppRole (`role_id` + `secret_id`, ce dernier chiffré et jamais réaffiché),
point de montage KV v2, préfixe des secrets de Fluxonata, autorité de certification privée si besoin, puis **Tester la connexion** (joindre, se connecter, écrire / relire / supprimer un secret d'essai)
et **Activer**. Un guide de mise en place (politique Vault, rôle) est dans l'écran. Pour un Vault du réseau interne, l'opérateur ajoute son adresse à `ORCH_EGRESS_ALLOWLIST`.
Chaque espace atteint sa zone `<préfixe>/<espace>/` ; d'autres chemins (par exemple `secret/partage/*`, `database/creds/*`) se donnent par **Accès par espace** (lecture seule ou écriture).
Ensuite, à la création d'un secret, d'une connexion ou d'un webhook signé, on choisit **le coffre de Fluxonata ou Vault** : valeur écrite par Fluxonata dans Vault, secret existant (chemin + clé + version),
ou identifiants dynamiques (le nom d'utilisateur est alors fourni par Vault). Administration → *Secrets* liste les secrets, leur emplacement, permet de tester la lecture (sans jamais montrer la valeur), de remplacer ou de supprimer
(avec, pour un secret écrit par Fluxonata, suppression facultative dans Vault). Si Vault ne répond pas, les étapes concernées échouent avec un message explicite ; rien n'est lu dans le coffre de Fluxonata à la place. Voir `docs/DECISIONS/0019-coffre-externe-vault.md`.

## Authentification
Locale (développement) ou **OIDC** (Keycloak, Authentik… ; flux code + PKCE). Variables : `ORCH_OIDC_ISSUER`, `ORCH_OIDC_CLIENT_ID`, `ORCH_OIDC_CLIENT_SECRET`,
`ORCH_OIDC_REDIRECT_URI`, `ORCH_OIDC_GROUP_ROLES` (JSON groupe → espace/rôle). Les comptes sont liés par (émetteur, `sub`), **jamais par e-mail**.
Un fournisseur d'identité interne doit figurer dans `ORCH_EGRESS_ALLOWLIST`. Testé contre un fournisseur simulé conforme ; **non testé contre un Keycloak réel**.

## Assistant IA (facultatif)
Modes : *désactivé* (défaut), *local strict* (aucun envoi cloud, aucun repli externe), *cloud autorisé* (fournisseur choisi explicitement, données masquées,
budget quotidien). Fournisseurs : serveur local compatible OpenAI (llama.cpp, Ollama, vLLM), API Messages d'Anthropic (facultatif, payant), faux fournisseur déterministe.
Toute génération reste un **brouillon** ; la décision d'autorisation n'est jamais prise par un modèle. Les licences des poids de modèles s'évaluent séparément du code.

**Serveur local (Ollama, llama.cpp, vLLM)** : le fournisseur de type « serveur local compatible OpenAI » prend l'adresse **racine** du serveur (par ex. `http://hote:11434` ; un `/v1` final est toléré). Par principe de sécurité (SSRF), la plate-forme refuse les adresses privées, y compris le serveur d'IA local : l'hôte doit être autorisé explicitement par l'exploitant via `ORCH_EGRESS_ALLOWLIST` (liste d'hôtes séparés par des virgules, pour l'API **et** les workers). Sous WSL, l'hôte Windows est l'adresse de la passerelle (`ip route | awk '/default/ {print $3}'`) et peut changer au redémarrage ; Ollama doit écouter au-delà de `localhost` (variable `OLLAMA_HOST=0.0.0.0` côté Windows, pare-feu à restreindre). Un modèle de 20–30 milliards de paramètres peut mettre une à deux minutes à répondre au premier appel (chargement) : réglez le délai du fournisseur en conséquence.

## Exploitation
Journaux JSON avec `correlation_id` (`X-Request-ID`), `run_id`, `execution_id` ; `GET /api/v1/health/live/` (vivacité) et `/health/ready/` (disponibilité) ;
métriques Prometheus sur `/api/v1/metrics/` si `ORCH_METRICS_TOKEN` est défini ; rétention : `manage.py prune_data`.

## Adresses internes autorisées (Plate-forme → Réseau)
Par principe (SSRF), le serveur refuse d'appeler une adresse interne (cette machine, un réseau privé). Un **administrateur de plate-forme** autorise ici, une par une, les adresses internes que les connexions, les listes dynamiques et les contrôles lus depuis un service doivent joindre :
- une adresse = un **nom d'hôte ou une IP exacts** (pas de plage, de joker, de schéma, de port ni de chemin) ; le port reste libre ;
- **jamais autorisables par l'interface** : métadonnées cloud, adresses de lien local (169.254.0.0/16, fe80::/10), « cet hôte » (0.0.0.0), multidiffusion et plages réservées ; un nom ajouté ici qui répond avec une telle adresse reste refusé ;
- l'ajout demande une **confirmation** explicite, concerne **tous les espaces**, s'applique en **quelques secondes** à l'API et aux workers (sans redémarrage) et est **journalisé** (« Adresse interne autorisée / retirée ») ; 100 adresses au plus ;
- les adresses de `ORCH_EGRESS_ALLOWLIST` (environnement) sont affichées en lecture seule : l'opérateur les garde sous sa responsabilité.
Les jetons d'API n'ont jamais ces droits (comme pour toute la plate-forme) ; l'autorisation est décidée par le serveur.

