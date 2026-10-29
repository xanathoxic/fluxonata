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
        parser.add_argument(
            "--vague",
            action="store_true",
            help="everyday-words requests: direct generation vs generation after the plan",
        )
        parser.add_argument("--edits", action="store_true", help="evaluate chat edits (operations) on a fixed workflow")
        parser.add_argument("--case", action="append", help="only this workflow case id (repeatable)")
        parser.add_argument(
            "--set",
            choices=("all", "tuned", "heldout"),
            default="all",
            help="tuned: cases the prompts were written against; heldout: written afterwards, never used to tune",
        )
        parser.add_argument(
            "--repeat", type=int, default=1, help="runs per case: a model varies from one run to the next"
        )

    def vague(self, ws, user, cfg, opts):
        """Requests written the way a non-expert does: generated directly, then after the assistant's plan (accepted as
        proposed, its questions keeping the proposed answers). Same plain checks on both."""
        import time

        from ai.evals import cases

        totals = {"direct": [0, 0, 0, 0.0], "plan": [0, 0, 0, 0.0]}  # ok, total, tokens, seconds
        for case in cases.VAGUE_CASES:
            if opts.get("case") and case["id"] not in opts["case"]:
                continue
            for run in range(opts["repeat"]):
                for mode in ("direct", "plan"):
                    t0, mark = time.time(), service._mark(ws)
                    try:
                        request = case["request"]
                        if mode == "plan":
                            outline = service.make_outline(ws, user, request, cfg=cfg)
                            request = service.outline_to_request(
                                request,
                                {
                                    **outline,
                                    "answers": [
                                        {"question": q["question"], "answer": q["proposed"]}
                                        for q in outline["questions"]
                                    ],
                                },
                            )
                        graph, issues, check, _ = service.build_draft(ws, user, request, "eval-essai", cfg=cfg)
                    except service.AiRefused as exc:
                        self.stdout.write(f"[{case['id']} {mode} #{run + 1}] refus : {exc.code}")
                        totals[mode][1] += len(case["checks"])
                        continue
                    results = []
                    for label, fn in case["checks"]:
                        try:
                            results.append((label, bool(fn(graph))))
                        except Exception:
                            results.append((label, False))
                    ok = sum(r for _, r in results)
                    used = service.usage_since(ws, mark)["tokens"]
                    t = totals[mode]
                    t[0], t[1], t[2], t[3] = t[0] + ok, t[1] + len(results), t[2] + used, t[3] + time.time() - t0
                    self.stdout.write(
                        f"[{case['id']} {mode} #{run + 1}] critères {ok}/{len(results)} · erreurs restantes {check['remaining']} · "
                        f"{used} jetons · {round(time.time() - t0)} s"
                    )
                    for label, good in results:
                        if not good:
                            self.stdout.write(f"    ✗ {label}")
        for mode, (ok, total, tokens, seconds) in totals.items():
            if total:
                self.stdout.write(f"  {mode} : {ok}/{total} critères · {tokens} jetons · {round(seconds)} s")

    def edits(self, ws, user, cfg, opts):
        """Chat edits: the same base workflow, one requested change each, scored by plain checks on the result."""
        import time

        from ai.evals import cases
        from ai.plan import compile_plan
        from workflows.models import Workflow

        base, _ = service.sanitize_draft(compile_plan(cases.EDIT_BASE_PLAN), ws, "eval-essai")
        cases._BASE_GRAPH[:] = [base]
        wf = Workflow.objects.filter(workspace=ws).first()
        grand_ok = grand_total = 0
        for case in cases.EDIT_CASES:
            if opts.get("case") and case["id"] not in opts["case"]:
                continue
            for run in range(opts["repeat"]):
                t0 = time.time()
                try:
                    out = service.edit_workflow(ws, user, wf, case["message"], [], base, cfg=cfg)
                except service.AiRefused as exc:
                    self.stdout.write(f"[{case['id']} #{run + 1}] refus : {exc.code}")
                    grand_total += len(case["checks"])
                    continue
                graph = out["graph"]
                results = []
                for label, fn in case["checks"]:
                    try:
                        results.append((label, bool(fn(graph, out))))
                    except Exception:
                        results.append((label, False))
                ok = sum(r for _, r in results)
                grand_ok, grand_total = grand_ok + ok, grand_total + len(results)
                self.stdout.write(
                    f"[{case['id']} #{run + 1}] critères {ok}/{len(results)} · {out['usage']['tokens']} jetons · "
                    f"{round(time.time() - t0)} s"
                )
                for label, good in results:
                    if not good:
                        self.stdout.write(f"    ✗ {label}")
                if ok < len(results):
                    self.stdout.write(f"    réponse : {out['reply'][:200]}")
        self.stdout.write(f"Total : {grand_ok}/{grand_total} critères.")

    def workflows(self, ws, user, cfg, opts):
        """Full generation pipeline (validation, corrections, review) on complex requests, scored by plain checks."""
        import time

        from django.db.models import Sum

        from ai.models import AiCall

        grand_ok = grand_total = 0
        split = {"tuned": [0, 0], "heldout": [0, 0]}
        tokens = seconds = runs = 0
        for case in WORKFLOW_CASES:
            if opts.get("case") and case["id"] not in opts["case"]:
                continue
            kind = "heldout" if case.get("heldout") else "tuned"
            if opts.get("set", "all") not in ("all", kind):
                continue
            for run in range(opts["repeat"]):
                before, t0 = AiCall.objects.filter(workspace=ws).count(), time.time()
                last = AiCall.objects.filter(workspace=ws).order_by("-pk").values_list("pk", flat=True).first() or 0
                try:
                    graph, issues, check, _ = service.build_draft(ws, user, case["request"], "eval-essai", cfg=cfg)
                except service.AiRefused as exc:
                    self.stdout.write(f"[{case['id']} #{run + 1}] refus : {exc.code}")
                    grand_total += len(case["checks"])
                    split[kind][1] += len(case["checks"])
                    continue
                results = []
                for label, fn in case["checks"]:
                    try:
                        results.append((label, bool(fn(graph))))
                    except Exception:  # a malformed answer fails the check, it does not stop the evaluation
                        results.append((label, False))
                ok = sum(r for _, r in results)
                grand_ok, grand_total = grand_ok + ok, grand_total + len(results)
                split[kind][0] += ok
                split[kind][1] += len(results)
                calls = AiCall.objects.filter(workspace=ws).count() - before
                used = AiCall.objects.filter(workspace=ws, pk__gt=last).aggregate(
                    t=Sum("input_tokens") + Sum("output_tokens")
                )["t"]
                tokens, seconds, runs = tokens + (used or 0), seconds + (time.time() - t0), runs + 1
                self.stdout.write(
                    f"[{case['id']} #{run + 1}] critères {ok}/{len(results)} · erreurs de validation restantes "
                    f"{check['remaining']} · remarques {len(check['findings'])} · {calls} appels · {used or 0} jetons · {round(time.time() - t0)} s"
                )
                for label, good in results:
                    if not good:
                        self.stdout.write(f"    ✗ {label}")
        for kind, (ok, total) in split.items():
            if total:
                self.stdout.write(f"  {kind} : {ok}/{total}")
        if runs:
            self.stdout.write(f"  moyenne par génération : {round(tokens / runs)} jetons, {round(seconds / runs)} s")
        self.stdout.write(f"Total : {grand_ok}/{grand_total} critères.")

    def handle(self, workspace, provider, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        cfg = AiProvider.objects.filter(workspace=ws, name=provider).first() if ws else None
        if cfg is None:
            raise CommandError("Espace ou fournisseur inconnu.")
        user = ws.memberships.first().user
        if opts.get("vague"):
            return self.vague(ws, user, cfg, opts)
        if opts.get("edits"):
            return self.edits(ws, user, cfg, opts)
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
