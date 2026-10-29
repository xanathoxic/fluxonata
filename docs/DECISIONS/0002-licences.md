# ADR 0002 — Licences et dépendances

Code original : **Apache-2.0** (`LICENSE`, `NOTICE`). Aucune fonctionnalité réservée, aucun compteur, aucune clé d'activation.
Les licences ci-dessous ont été **lues dans les métadonnées des paquets installés le 2026-10-25** (pas seulement de mémoire). `pip-audit` et `npm audit` (vulnérabilités) tournent en CI ;
un contrôle automatique des licences reste à ajouter (les SBOM CycloneDX générés par la CI listent toutes les dépendances transitives).

## Exécution (backend)
| Dépendance | Version | Licence | Remarque |
|---|---|---|---|
| Django | 6.1.2 | BSD-3-Clause | |
| djangorestframework | 3.18.3 | BSD-3-Clause | |
| drf-spectacular | 0.30.0 | BSD-3-Clause | Swagger UI non servi (évite un CDN externe) |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0 | bibliothèque non modifiée, liée dynamiquement ; conserver la notice |
| PyMySQL | 1.2.3 | MIT | étape « Requête SQL » sur MySQL / MariaDB (lecture seule) |
| paramiko | 5.1.0 | LGPL-2.1 | étape « Fichier SFTP » ; bibliothèque non modifiée, liée dynamiquement ; conserver la notice |
| ldap3 | 2.9.1 | LGPL-3.0 | étape « Annuaire LDAP » (lecture seule) ; bibliothèque non modifiée, liée dynamiquement ; conserver la notice |
| jsonschema | 4.26.0 | MIT | |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | chiffrement (Fernet), signatures Ed25519 |
| joserfc | 1.7.7 | BSD-3-Clause | vérification des jetons d'identité (JWT/JWKS) |
| gunicorn | 26.2.0 | MIT | serveur d'application de production |

## Interface
| Dépendance | Version | Licence |
|---|---|---|
| react, react-dom | 19.3.1 | MIT |
| react-router-dom | 7.18.4 | MIT |
| @tanstack/react-query | 5.104.5 | MIT |
| @xyflow/react | 12.12.0 | MIT |
| vite / @vitejs/plugin-react | 8.3.5 / 6.1.2 | MIT |
| typescript | 7.0.2 | Apache-2.0 |

## Outils de développement et de test (non distribués)
| Outil | Version | Licence | Remarque |
|---|---|---|---|
| ruff | 0.17.2 | MIT | |
| pytest, pytest-django | 9.1.1 / 4.14.0 | MIT / BSD-3-Clause | |
| vitest + Testing Library | 5.0.5 | MIT | |
| @playwright/test | 1.64.1 | Apache-2.0 | |
| @axe-core/playwright | 4.14.0 | **MPL-2.0** | usage de test uniquement, non distribué ; copyleft au niveau du fichier |
| cyclonedx-bom | 7.5.0 | Apache-2.0 | SBOM |
| @codemirror/* (view, state, language, commands, autocomplete), @lezer/highlight | 6.x | MIT | éditeur de code |
| @fontsource-variable/plus-jakarta-sans | 5.x | OFL-1.1 (police, embarquée localement, aucun CDN) | thème visuel |
| pip-audit | 2.10.1 | (non renseignée dans les métadonnées ; licence amont Apache-2.0 — **à confirmer**) | audit de dépendances |

## Images (infrastructure)
`postgres:17-alpine` (licence PostgreSQL), `python:3.13-slim` (PSF/Debian), `node:22-slim` (MIT/Debian), `nginx:1.27-alpine` (BSD-2-Clause),
`mcr.microsoft.com/powershell` (MIT pour PowerShell ; image Debian). Les images ne sont pas épinglées par digest par défaut ; `scripts/pin-images.sh` affiche les références immuables.

## Obligations
Conserver `LICENSE`/`NOTICE` ; fournir le texte LGPL-3.0 de psycopg et la possibilité de le remplacer (liaison dynamique, non modifié) ; ne pas redistribuer les poids de modèles d'IA sous l'Apache-2.0 du code
(leurs licences propres s'appliquent). Aucune bibliothèque payante n'est requise.
