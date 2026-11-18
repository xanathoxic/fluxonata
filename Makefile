# Fluxonata — raccourcis d'exploitation : développement, production (Docker Compose) et mise à jour de la plateforme.
#   make help                 liste des commandes
# Production : topologie compacte par défaut ; `TOPO=roles` pour la topologie par rôles ; `EXTRA="-f infra/docker-compose.agent.yml"` pour ajouter un fichier
# Compose (voir docs/INSTALL.md et docs/RUNNERS.md). Les commandes de production se lancent sur le serveur, dans le dossier du dépôt.

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

TOPO  ?= compact
EXTRA ?=
FILES := -f infra/docker-compose.prod.yml$(if $(filter roles,$(TOPO)), -f infra/docker-compose.roles.yml) $(EXTRA)
C     := docker compose $(FILES) --env-file infra/.env.prod
BACKUPS ?= backups

.PHONY: help
help: ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- développement
.PHONY: dev-up dev-down dev-reset-db dev-restart dev-doctor dev-smoke
dev-up: ## Développement : démarre la pile (base, API, orchestrateur, worker, interface)
	scripts/dev-up.sh
dev-down: ## Développement : arrête les processus (la base est conservée)
	scripts/dev-down.sh
dev-reset-db: ## Développement : arrête tout ET la base (données perdues ; exige CONFIRM=oui)
	@[ "$(CONFIRM)" = "oui" ] || { echo "Destructif : relancez avec CONFIRM=oui"; exit 2; }
	scripts/dev-down.sh --db
dev-restart: ## Développement : redémarre la pile (recharge le code de l'API)
	scripts/dev-down.sh
	scripts/dev-up.sh
dev-doctor: ## Développement : diagnostic de la pile
	scripts/doctor.sh
dev-smoke: ## Développement : essai rapide de bout en bout
	scripts/smoke.sh

# ---------------------------------------------------------------- production
.PHONY: prod-init prod-up prod-down prod-restart prod-ps prod-logs prod-bootstrap prod-backup prod-verify prod-update prod-rollback
prod-init: ## Production : crée infra/.env.prod (secrets aléatoires ; jamais écrasé)
	scripts/prod-init.sh
prod-up: ## Production : construit et démarre (migrations appliquées avant l'API)
	$(C) up -d --build
prod-down: ## Production : arrête les services (volumes conservés)
	$(C) down
prod-restart: ## Production : redémarre les services sans reconstruire
	$(C) restart
prod-ps: ## Production : état des services
	$(C) ps
prod-logs: ## Production : journaux (SERVICE=api pour un seul service)
	$(C) logs --tail=200 -f $(SERVICE)
prod-bootstrap: ## Production : PREMIÈRE installation seulement (exige CONFIRM=oui ; WORKSPACE=principal)
	@[ "$(CONFIRM)" = "oui" ] || { echo "À ne lancer qu'une fois, à la première installation : relancez avec CONFIRM=oui"; exit 2; }
	$(C) exec api python manage.py bootstrap --workspace $(or $(WORKSPACE),principal)

prod-backup: ## Production : sauvegarde (base, clés, artefacts) dans backups/<date>/ — à copier hors du serveur
	TS=$$(date -u +%Y%m%dT%H%M%SZ); D="$(BACKUPS)/$$TS"
	mkdir -p "$$D"; chmod 700 "$$D"
	git rev-parse HEAD > "$$D/commit.txt"
	$(C) exec -T db pg_dump -U fluxonata -Fc fluxonata > "$$D/db.dump"
	docker run --rm -v fluxonata-prod_keys:/d:ro -v "$$PWD/$$D":/out alpine tar -C /d -czf /out/keys.tar.gz .
	docker run --rm -v fluxonata-prod_artifacts:/d:ro -v "$$PWD/$$D":/out alpine tar -C /d -czf /out/artifacts.tar.gz .
	ls -lh "$$D"; echo "Sauvegarde : $$D (gardez keys.tar.gz séparé du reste, et hors du serveur)"

prod-verify: ## Production : état, migrations appliquées, page d'accueil (URL=http://127.0.0.1:8080/)
	$(C) ps
	$(C) logs migrate | tail -15
	PENDING=$$($(C) exec -T api python manage.py showmigrations | grep -c "\[ \]" || true)
	echo "Migrations en attente : $$PENDING (attendu : 0)"
	CODE=$$(curl -s -o /dev/null -w "%{http_code}" $(or $(URL),http://127.0.0.1:8080/) || true)
	echo "Page d'accueil : HTTP $$CODE (attendu : 200)"
	[ "$$PENDING" = "0" ] && [ "$$CODE" = "200" ]

# `prod-update` et `prod-rollback` sont des CHAÎNES d'étapes (jamais d'appel à $(MAKE) dans une recette : `make -n` reste une vraie simulation).
.NOTPARALLEL:
.PHONY: _clean-tree _pull _restore
_clean-tree:
	if ! git diff --quiet || ! git diff --cached --quiet; then
	  echo "Des fichiers suivis par Git sont modifiés : sauvegardez-les ou annulez-les avant la mise à jour (git status)."; exit 2
	fi
_pull:
	git fetch origin
	echo "Avant : $$(git rev-parse --short HEAD)"
	git pull --ff-only || { echo "Pull impossible sans fusion (historique divergent ?) : voir docs/INSTALL.md §7.3. Rien n'a été reconstruit."; exit 2; }
	echo "Après : $$(git rev-parse --short HEAD)"
prod-update: _clean-tree prod-backup _pull prod-up prod-verify ## Production : mise à jour = sauvegarde, git pull --ff-only, reconstruction, vérification
	@echo "Mise à jour terminée. Ouvrez Administration → Santé. Retour arrière : make prod-rollback BACKUP=$(BACKUPS)/<date> CONFIRM=oui"

prod-rollback: _restore prod-up prod-verify ## Production : retour arrière depuis une sauvegarde (BACKUP=backups/<date> CONFIRM=oui) — remplace la base
_restore:
	@[ "$(CONFIRM)" = "oui" ] && [ -n "$(BACKUP)" ] && [ -f "$(BACKUP)/db.dump" ] || { echo "Usage : make prod-rollback BACKUP=backups/<date> CONFIRM=oui (remplace la base par la sauvegarde)"; exit 2; }
	$(C) stop api api-ui api-ingest orchestrator worker worker-exec worker-aux worker-ai runner-gw web 2>/dev/null || true
	$(C) exec -T db dropdb -U fluxonata fluxonata
	$(C) exec -T db createdb -U fluxonata fluxonata
	$(C) exec -T db pg_restore -U fluxonata -d fluxonata --no-owner < "$(BACKUP)/db.dump"
	if [ -f "$(BACKUP)/commit.txt" ]; then git reset --hard "$$(cat $(BACKUP)/commit.txt)"; fi

# ---------------------------------------------------------------- contrôles du projet
.PHONY: ci ci-fast test-backend test-frontend
ci: ## Tous les contrôles (ruff, migrations, pytest, build, vitest, audits, E2E)
	scripts/ci.sh
ci-fast: ## Tous les contrôles sauf l'E2E
	scripts/ci.sh --skip-e2e
test-backend: ## Tests backend (PostgreSQL + Docker requis)
	cd backend && ../.venv/bin/python -m pytest
test-frontend: ## Build et tests du frontend
	cd frontend && npm run build && npm test
