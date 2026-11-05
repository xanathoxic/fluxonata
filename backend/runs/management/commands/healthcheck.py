"""Sonde de santé d'un process SANS port HTTP (orchestrateur, worker) — pour `healthcheck:` de Docker/Kubernetes (AR2.6).

    python manage.py healthcheck --kind orchestrator|worker|worker-aux [--max-age 30]

Sort 0 si la base répond ET que CE process (même nom d'hôte = même conteneur) a battu du cœur depuis moins de `--max-age` secondes ; sinon 1 avec la raison sur
la sortie d'erreur. Un process dont la boucle est bloquée (et donc qui ne bat plus) est ainsi vu comme malade, pas seulement un process disparu.
"""

import socket
import sys

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from runs.models import ServiceInstance

KINDS = ("orchestrator", "worker", "worker-aux")


class Command(BaseCommand):
    help = "Santé d'un process sans port HTTP : base joignable et battement de cœur récent de ce conteneur."

    def add_arguments(self, parser):
        parser.add_argument("--kind", required=True, choices=KINDS)
        parser.add_argument("--max-age", type=float, default=30.0, help="secondes (défaut 30)")

    def handle(self, *args, kind, max_age, **opts):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            return self.fail(f"base de données injoignable ({type(exc).__name__})")
        mine = (
            ServiceInstance.objects.filter(kind=kind, hostname=socket.gethostname()).order_by("-last_seen_at").first()
        )
        if mine is None:
            return self.fail(f"aucun process « {kind} » enregistré sur {socket.gethostname()}")
        age = (timezone.now() - mine.last_seen_at).total_seconds()
        if age > max_age:
            return self.fail(
                f"« {kind} » n'a plus battu du cœur depuis {age:.0f} s (limite {max_age:.0f} s) : sa boucle est bloquée ou arrêtée"
            )
        self.stdout.write(f"ok : {kind} {mine.name}, dernier battement il y a {age:.0f} s")

    def fail(self, message):
        self.stderr.write(f"MALADE : {message}")
        sys.exit(1)
