# Laboratoire Ansible : valider l'implémentation sur de vraies machines

Ce laboratoire installe, sur la machine de développement, tout ce qu'il faut pour **éprouver Ansible dans l'application contre de vraies machines SSH** : trois conteneurs jetables, un **runner dédié** qui voit leur réseau, une connexion qui porte la clé SSH, des inventaires, des rôles, des actions-playbooks et **un workflow qui vérifie chaque point**. Rien ici n'est un secret de production : clés synthétiques, machines éphémères.

## Mise en route (quatre commandes)

```bash
scripts/ansible-lab.sh up            # construit l'image des machines, crée les clés, le réseau et les trois conteneurs
```
```bash
cd backend && ../.venv/bin/python manage.py seed_ansible_lab    # installe connexion, inventaires, rôles, actions, runner, workflow
```
```bash
scripts/ansible-lab.sh agent-up      # démarre le runner dédié (l'agent réel) sur ce réseau
```
```bash
cd backend && ../.venv/bin/python manage.py check_ansible_lab   # lance le workflow, valide l'accord, dresse la liste des points
```

Prérequis : la pile de développement (`scripts/dev-up.sh`), Docker et l'image Ansible (`scripts/build-ansible-runner.sh`). Le réglage `ORCH_WORKER_TAGS=ansible` n'est **pas** nécessaire ici : les actions du laboratoire exigent l'étiquette `ansible-lab`, que seul le runner du laboratoire porte.

## Ce qui est installé

| Élément | Détail |
|---|---|
| **Machines** | `flx-lab-web1`, `flx-lab-web2` (groupe `web`) et `flx-lab-db1` (groupe `db`) : Alpine, OpenSSH, Python, sudo. Compte `deploy` (clé seulement, sudo sans mot de passe). Réseau Docker `fluxonata-ansible-targets`. |
| **Runner** | `lab-ansible` : agent réel, dédié à l'espace, langage `ansible`, étiquettes `ansible-lab` et `ansible`. Il lance chaque playbook dans un conteneur jetable rattaché au réseau des machines. |
| **Connexion** | `lab-ssh` (secret `lab-ssh-key`) : la clé privée, en base64 sur une ligne, injectée dans `FLX_SSH_KEY_B64`. |
| **Inventaire `lab`** | Groupes `web`, `db`, `labo` (parent des deux) ; variables communes (`ansible_user`, `lab_env`…) et de groupe (`http_port` 8080, `db_port` 5433) ; une machine **désactivée** (`flx-lab-eteinte`, jamais jouée). |
| **Inventaire `lab-panne`** | Une machine du laboratoire et une machine qui n'existe pas (injoignable). |
| **Rôles** | `common` (répertoire `/opt/lab`, fiche d'identité, handler), `web` et `db` (dépendent de `common` ; modèles Jinja, fichier copié, handlers), `controle` (relit et vérifie ce qui a été écrit). |
| **Actions** | `lab-ping`, `lab-nettoyer`, `lab-deployer` (rôles `common`, `web`, `db`), `lab-controle` (rôle `controle`, appelé par `include_role`). |
| **Workflow** | `lab-ansible-complet` (dossier « Démonstration/Ansible (laboratoire) »). |

## Ce que le workflow éprouve, dans l'ordre

1. **Joindre les machines** : SSH par la clé du coffre, clés d'hôte vérifiées (`known_hosts` strict), inventaire géré (trois machines jouées, la désactivée exclue), runner dédié.
2. **Une machine injoignable** : `fail_on_error: false` ; le workflow continue, le résultat dit `ok: false` et un hôte injoignable.
3. **Remise à zéro** (`become`) : le workflow repart toujours d'un état propre ; il est relançable à volonté.
4. **Essai à blanc** (`check`) : Ansible annonce des changements sans rien modifier.
5. **Accord d'une personne** (approbation).
6. **Déploiement réel** : `roles:` avec dépendance (`common` avant `web` et `db`), modèles, fichier copié, handlers, élévation de privilèges, variables de groupe.
7. **Second passage** : **idempotence** (zéro changement).
8. **Contrôles** : `include_role` et assertions sur l'état réel des fichiers, machine par machine.
9. **`limit` et `tags`** : seules les machines `web`, seules les tâches `web`.
10. **Bilan** : un script dresse la liste des points validés ; les sorties du workflow sont `valide`, `resume` et `points`.

`check_ansible_lab` ajoute les points que seule la plate-forme peut juger : **tous les playbooks ont été joués par le runner `lab-ansible`** (et aucun par le worker intégré), **rôles figés** dans l'action (versions exactes, dépendances comprises), **la clé SSH n'apparaît nulle part** (journaux, résultats, sorties), **l'inventaire n'est pas recopié** dans l'exécution enregistrée (seule la référence l'est), accord donné par une autre personne.

Dans l'interface : lancez `lab-ansible-complet` avec un message ; validez l'accord (Approbations, avec `claire`) ; ouvrez l'exécution, onglets **Ansible** (bilan par machine, tâches, journal) et **Entrées/Sorties**.

## Exploitation

| Commande | Effet |
|---|---|
| `scripts/ansible-lab.sh status` | État des trois machines |
| `scripts/ansible-lab.sh agent-down` | Arrête le runner |
| `scripts/ansible-lab.sh down` | Supprime les conteneurs et le réseau (les clés restent) |
| `scripts/ansible-lab.sh clean` | `down` + supprime le dossier `var/ansible-lab/` (clés comprises) |
| `manage.py seed_ansible_lab --purge` | Retire tout de l'espace (workflow, actions, rôles, inventaires, connexion, runner) |

Après un `--purge` suivi d'un nouveau `seed_ansible_lab`, le runner reçoit un **nouveau jeton** : relancez `agent-down` puis `agent-up`.

## Limites

- Les machines sont des conteneurs Alpine sur le même hôte Docker que l'agent : ce n'est pas un essai sur un réseau distant ni sur d'autres systèmes (Windows, réseau).
- Le test automatique `tests/test_ansible_lab.py` rejoue tout cela de bout en bout (machines, agent, workflow) et nécessite Docker, l'image Ansible et de quoi construire l'image des machines (téléchargement des paquets Alpine).
