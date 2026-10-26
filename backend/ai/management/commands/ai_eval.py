from django.core.management.base import BaseCommand, CommandError

from ai import service
from ai.evals.cases import DRAFT_CASES, SEARCH_CASES, WORKFLOW_CASES
from ai.models import AiProvider
from tenancy.models import Workspace


class Command(BaseCommand):
    help = "Evaluate a configured provider on the fixed case set (use a local model; cloud runs cost money)."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("--provider", required=True)
        parser.add_argument("--workflows", action="store_true", help="evaluate complex workflow generation (slow)")
        parser.add_argument("--case", action="append", help="only this workflow case id (repeatable)")
        parser.add_argument(
            "--repeat", type=int, default=1, help="runs per case: a model varies from one run to the next"
        )

    def workflows(self, ws, user, cfg, opts):
        """Full generation pipeline (validation, corrections, review) on complex requests, scored by plain checks."""
        import time

        from ai.models import AiCall

        grand_ok = grand_total = 0
        for case in WORKFLOW_CASES:
            if opts.get("case") and case["id"] not in opts["case"]:
                continue
            for run in range(opts["repeat"]):
                before, t0 = AiCall.objects.filter(workspace=ws).count(), time.time()
                try:
                    graph, issues, check, _ = service.build_draft(ws, user, case["request"], "eval-essai", cfg=cfg)
                except service.AiRefused as exc:
                    self.stdout.write(f"[{case['id']} #{run + 1}] refus : {exc.code}")
                    grand_total += len(case["checks"])
                    continue
                results = []
                for label, fn in case["checks"]:
                    try:
                        results.append((label, bool(fn(graph))))
                    except Exception:  # a malformed answer fails the check, it does not stop the evaluation
                        results.append((label, False))
                ok = sum(r for _, r in results)
                grand_ok, grand_total = grand_ok + ok, grand_total + len(results)
                calls = AiCall.objects.filter(workspace=ws).count() - before
                self.stdout.write(
                    f"[{case['id']} #{run + 1}] critères {ok}/{len(results)} · erreurs de validation restantes "
                    f"{check['remaining']} · remarques {len(check['findings'])} · {calls} appels · {round(time.time() - t0)} s"
                )
                for label, good in results:
                    if not good:
                        self.stdout.write(f"    ✗ {label}")
        self.stdout.write(f"Total : {grand_ok}/{grand_total} critères.")

    def handle(self, workspace, provider, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        cfg = AiProvider.objects.filter(workspace=ws, name=provider).first() if ws else None
        if cfg is None:
            raise CommandError("Espace ou fournisseur inconnu.")
        user = ws.memberships.first().user
        if opts.get("workflows"):
            return self.workflows(ws, user, cfg, opts)
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
