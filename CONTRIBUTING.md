# Contribuer à Fluxonata

Merci de votre intérêt ! Fluxonata est un projet open source (Apache-2.0). Les contributions — corrections, tests, documentation, fonctionnalités — sont bienvenues.

## Avant de commencer
- **Faille de sécurité** : ne l'ouvrez pas en ticket public, suivez [SECURITY.md](SECURITY.md).
- **Bug ou idée** : ouvrez un ticket (*issue*) en décrivant le contexte, les étapes de reproduction et le résultat attendu. Pour une fonctionnalité importante, discutons-en avant de coder.
- En contribuant, vous acceptez que votre contribution soit publiée sous licence [Apache-2.0](LICENSE).

## Mettre en place l'environnement
Prérequis : Linux ou WSL2, Docker, Python 3.12+, Node 22+. Voir [docs/INSTALL.md](docs/INSTALL.md).
```bash
git clone https://github.com/xanathoxic/fluxonata.git && cd fluxonata
scripts/dev-up.sh          # pile de développement avec données fictives (comptes alice/bob/claire/admin)
```
Après une modification du **backend**, redémarrez la pile (`scripts/dev-down.sh && scripts/dev-up.sh`) : l'API de développement ne se recharge pas toute seule.

## Vérifier avant d'ouvrir une demande de fusion
```bash
cd backend && ../.venv/bin/ruff format . ../runner && ../.venv/bin/ruff check . ../runner
cd backend && ../.venv/bin/python -m pytest                  # PostgreSQL + Docker requis (≈ 4 min)
cd frontend && npx tsc --noEmit && npm run build && npm test
scripts/e2e.sh                                               # navigateur + accessibilité (pile lancée)
scripts/ci.sh                                                # tout, d'un coup
```
Ne lancez pas deux suites en parallèle (elles partagent la base de test et la pile de développement).

## Règles du projet
- **Autorisation toujours côté serveur** : l'interface ne fait que masquer des boutons. Toute nouvelle route d'espace passe par `get_workspace(...)` avec le droit adapté et est couverte par le balayage d'isolation.
- **Aucun secret** dans le dépôt, les journaux ni les invites d'IA ; aucune télémétrie ; aucune fonctionnalité « enterprise », compteur ou clé d'activation.
- **Jamais d'exécution de code dans le processus web** : les actions s'exécutent dans des conteneurs (workers, runners).
- **Tests** : tout changement de comportement s'accompagne d'un test. Un test qui échoue se corrige à la source, on n'affaiblit pas un test pour le faire passer.
- **Interface en français**, accessible (WCAG 2.2 AA) : libellés associés aux champs, navigation clavier, pas d'information portée par la seule couleur. Les contrôles axe-core de `frontend/e2e/` doivent rester verts.
- **TypeScript strict**, pas de `any` injustifié ; Python formaté et vérifié par `ruff`.
- **Migrations** additives ; `manage.py makemigrations --check` fait partie de la CI.
- Soyez honnête sur les limites : un comportement non testé est signalé comme tel dans la description de la demande de fusion.

## Messages de commit et demandes de fusion
- Messages courts à l'impératif ou au présent, en français, expliquant le *pourquoi* au besoin.
- Une demande de fusion = un sujet. Décrivez ce qui change, comment vous l'avez vérifié (commandes exécutées) et ce qui n'est **pas** couvert.
- Mettez à jour la documentation (`docs/`, captures si l'interface change : `scripts/screenshots.sh`) et, si l'API change, `docs/openapi.json`
  (`cd backend && ../.venv/bin/python manage.py spectacular --format openapi-json --file ../docs/openapi.json`).

## Structure du dépôt
| Dossier | Contenu |
|---|---|
| `backend/` | Django/DRF : `tenancy`, `catalog`, `workflows`, `runs`, `triggers`, `vault`, `bundles`, `ai`, `audit`, `core` |
| `frontend/` | React 19 + TypeScript strict ; éditeur dans `src/workflow/` ; tests E2E dans `e2e/` |
| `workers/` | harnais d'exécution des actions (Python, Node), protocole v1 |
| `runner/` | agent runner autonome (bibliothèque standard Python) |
| `infra/` | Docker Compose (développement, production), Dockerfiles, nginx |
| `scripts/` | démarrage, vérifications, sauvegardes, résilience, captures |
| `docs/` | documentation d'installation et d'utilisation |
