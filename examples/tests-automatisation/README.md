# Tests d'automatisation encadrés

Des scénarios **prêts à rejouer** pour vérifier, avec de vrais workflows, que la plate-forme fait bien ce qu'on lui demande
(réception d'un e-mail, analyse, décision, approbation, rapport, lot, base de données, secrets, agent IA…) **sans jamais
toucher à de vrais systèmes ni à la production**.

| Fichier | Rôle |
|---|---|
| [`01-plan-de-tests.md`](01-plan-de-tests.md) | **À lire en premier** : le cadre (ce qui rend ces tests inoffensifs), les prérequis et le détail de chaque test |
| [`scenarios/`](scenarios/) | Un fichier par test : prompts à copier dans l'assistant IA, données d'essai, résultat attendu, vérifications, nettoyage |
| [`ressources/`](ressources/) | **À importer sur une plate-forme vierge** : paquet d'actions de démonstration et deux skills IA |
| [`outils/installer_ressources.py`](outils/installer_ressources.py) | Installe ces ressources par l'API (`--dry-run` pour voir) ; `construire_paquet.py` écrit l'archive pour l'import par l'interface |
| [`outils/envoyer_webhook.py`](outils/envoyer_webhook.py) | Simule l'arrivée d'un événement (un e-mail, une alerte) en appelant un webhook signé |

Ces scénarios servent à deux choses : **essayer** l'automatisation avec l'assistant IA (« Générer avec l'IA », « Assistant IA »),
et **vérifier** une installation ou une nouvelle version (le résultat attendu est écrit pour chaque test).
