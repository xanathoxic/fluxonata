# Banc de référence sur hôte dédié (AR0.3) — mode d'emploi

**Statut : NON EXÉCUTÉ.** Toutes les mesures de ce dépôt ont été faites sur une machine de développement partagée (WSL2 + Docker Desktop, `fsync` lent, d'autres conteneurs voisins : GitLab, Open WebUI…). Elles disent
qu'une cause est identifiée ou qu'un défaut est corrigé ; elles **ne disent pas** quelle capacité tient une installation. Ce document liste ce qu'il faut rejouer sur un hôte **dédié et inactif** pour que les
objectifs de `docs/ARCHITECTURE.md` § 6 deviennent des mesures.

## Précautions (à consigner avec les résultats)
Processeur, mémoire, disque (et son `fsync` : `pg_test_fsync`), version de Docker, aucun autre conteneur, hôte au repos avant chaque mesure (charge < 0,3), version/commit mesurés. **Ne jamais modifier un objectif après la mesure** (voir `LOAD_TEST.md` : les objectifs sont posés avant).

## Mesures à rejouer, dans l'ordre
| # | Objectif visé | Commande | Résultat de référence (machine partagée) |
|---|---|---|---|
| 1 | S4/S5 : lecture et soumission p95 | `scripts/loadtest.sh` (O1, O2) | O1 p95 193 → 334 ms ; O2 p95 213 ms |
| 2 | S7 : débit du moteur seul | `scripts/loadtest.sh` (O4) | 763 runs/min |
| 3 | S8 / AR4.6 : passage à l'échelle de l'exécution | `scripts/loadtest.sh` (O3b) puis `scripts/loadtest_worker_concurrency.py --actions 16 --concurrency 1 2 4 8` | ×1,40 (O3b) ; ×1,23 (4 créneaux) — **non atteint** |
| 4 | AR4.4 : coût du tour de l'orchestrateur | `scripts/loadtest_orchestrator.py --runs 100 500 2000` | 3 ms pour 800 runs en attente |
| 5 | S6 / AR1.3 : interface pendant des attentes longues | `scripts/loadtest_runners.py --runners 60 100 --slots 100 4` | 289 réponses, max 500 ms (4 créneaux) |
| 6 | S1–S3 : pannes réelles | `scripts/resilience.sh` | 8/8 |
| 7 | S12 : mise à jour progressive sous charge | `scripts/rolling_upgrade_test.py --old-ref <tag> --replicas 2` | 0 perte, 0 erreur UI avec garde de santé |
| 8 | S10/S11 : restauration | `scripts/restore-test.sh`, `scripts/pitr-test.sh` (puis sur une base de la taille de la production) | PITR 19 s sur une base minuscule |
| 9 | S9 : délai déclencheur → début de run | **à écrire** : webhook signé, mesurer l'écart entre l'acquittement et `step_started` hors file d'actions | non mesuré |
| 10 | S13 : voisin bruyant | **à écrire** : un espace sature la file, mesurer la latence de prise d'un autre (`tests/test_fairness.py` couvre la correction, pas la latence) | non mesuré |

## Ce qui reste hors de portée d'un hôte unique
Haute disponibilité de PostgreSQL (AR5.1), bascule sous charge, exécuteur Kubernetes, gVisor/Kata, mise à l'échelle sur plusieurs hôtes d'exécution : ils demandent plusieurs machines ou un cluster.
