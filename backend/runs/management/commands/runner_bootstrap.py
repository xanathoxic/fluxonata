"""Enregistre (ou met à jour) un runner dont le JETON est fourni par l'environnement — pour les déploiements automatisés (AR3.2, ADR 0023).

    ORCH_EXEC_AGENT_TOKEN=orr_… python manage.py runner_bootstrap [--name exec-agent] [--token-env ORCH_EXEC_AGENT_TOKEN] [--max-concurrent 2]

Idempotent : le runner n'est créé qu'une fois ; si le jeton de l'environnement a changé, il est REMPLACÉ (l'environnement fait foi). Seule l'empreinte du jeton est écrite en base ; le jeton
n'est jamais affiché ni journalisé. Un runner créé ainsi est « partagé » (tous les espaces) ; pour un runner dédié à des espaces, utiliser l'interface d'administration.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from audit.models import record
from runs.models import Runner
from runs.runner_admin import ALL_RUNTIMES
from runs.runner_auth import TOKEN_PREFIX, hash_token


class Command(BaseCommand):
    help = "Enregistre un runner partagé dont le jeton vient de l'environnement (idempotent, jeton jamais affiché)."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="exec-agent")
        parser.add_argument("--token-env", default="ORCH_EXEC_AGENT_TOKEN")
        parser.add_argument("--max-concurrent", type=int, default=2)

    def handle(self, *args, name, token_env, max_concurrent, **opts):
        raw = os.environ.get(token_env, "").strip()
        if not raw.startswith(TOKEN_PREFIX) or len(raw) < 32:
            raise CommandError(
                f"Variable {token_env} absente ou invalide (attendu : « {TOKEN_PREFIX}… », 32 caractères au moins ; voir scripts/prod-init.sh)."
            )
        digest = hash_token(raw)
        existing = Runner.objects.filter(name=name).first()
        if existing is not None and existing.token_hash == digest:
            self.stdout.write(f"Runner « {name} » déjà enregistré : rien à faire.")
            return
        if Runner.objects.filter(token_hash=digest).exclude(name=name).exists():
            raise CommandError("Ce jeton appartient déjà à un autre runner.")
        created = existing is None
        if created:
            runner = Runner.objects.create(
                name=name,
                scope=Runner.Scope.SHARED,
                token_hash=digest,
                token_prefix=raw[:12],
                max_concurrent=max(1, min(16, max_concurrent)),
                runtimes=list(
                    ALL_RUNTIMES
                ),  # SANS langages autorisés le runner n'est éligible à aucune tâche : défaut trouvé sur la vraie pile
                description="Agent d'exécution (ADR 0023)",
            )
        else:  # jeton remplacé ; les réglages faits à la main (langages, pause, parallélisme) sont conservés
            runner = existing
            runner.token_hash, runner.token_prefix = digest, raw[:12]
            if not runner.runtimes:
                runner.runtimes = list(ALL_RUNTIMES)
            runner.save()
        record("runner.bootstrap", actor=None, workspace=None, target=runner, name=name, created=created)
        self.stdout.write(f"Runner « {name} » {'créé' if created else 'mis à jour : jeton remplacé'} (partagé).")
