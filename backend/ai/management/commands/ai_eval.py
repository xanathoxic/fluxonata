from django.core.management.base import BaseCommand, CommandError

from ai import service
from ai.evals.cases import DRAFT_CASES, SEARCH_CASES
from ai.models import AiProvider
from tenancy.models import Workspace


class Command(BaseCommand):
    help = "Evaluate a configured provider on the fixed case set (use a local model; cloud runs cost money)."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("--provider", required=True)

    def handle(self, workspace, provider, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        cfg = AiProvider.objects.filter(workspace=ws, name=provider).first() if ws else None
        if cfg is None:
            raise CommandError("Espace ou fournisseur inconnu.")
        user = ws.memberships.first().user
        ok = total = 0
        for c in SEARCH_CASES:
            total += 1
            try:
                got = [
                    m["slug"] for m in service.catalog_search(ws, user, c["query"], provider_name=provider)["matches"]
                ]
                hit = c["expect"] in got
            except service.AiRefused as exc:
                hit = False
                self.stdout.write(f"  refus : {exc.code}")
            ok += hit
            self.stdout.write(f"[{'OK' if hit else 'KO'}] recherche « {c['query']} »")
        self.stdout.write(
            f"{ok}/{total} cas réussis (contrôles structurels uniquement ; la qualité rédactionnelle n'est pas mesurée)."
        )
        _ = DRAFT_CASES
