"""Essai à blanc d'un brouillon pour le MCP de conception (ADR 0025) : l'assistant ESSAIE ce qu'il a écrit au lieu de deviner.

Ce qui s'exécute réellement : les scripts et les décisions du brouillon, dans le bac à sable isolé (ni réseau, ni fichiers, ni secrets, limites de temps
et de mémoire). Ce qui est SIMULÉ d'après son contrat : actions, étapes IA, sous-workflows, approbations, attentes (le test d'une action proposée est
un autre outil). Rien n'est publié, enregistré ni envoyé.

Règle du dépôt : jamais d'exécution dans le process web. `start_dry_run` ne fait que mettre un travail en file (`AiJob`, genre « dryrun ») ;
c'est le worker (rôle `ai`) qui l'exécute (`run_job`, appelé par `ai.jobs.process`) ; `get_dry_run` ne fait que lire l'avancement et le résultat."""

import json

from django.http import Http404
from rest_framework import serializers

from audit.models import record
from tenancy.permissions import EDIT, VIEW, has_perm

from . import access

MAX_SAMPLES = 3  # le moteur n'en exploite pas davantage
MAX_ACTIVE_JOBS = 2  # essais en cours ou en attente par personne
MAX_CASES = 5  # cas par test d'action
ACTION_TIMEOUT = 20  # secondes par cas
ACTION_MEMORY_MB = 256
MAX_STEPS = (
    250  # plus que le plafond de 200 étapes d'un workflow : un essai complet ne s'arrête jamais « faute de pas »
)
BUDGET_SECONDS = 240
DETAIL = 160
OUTPUT = 120


class SampleIn(serializers.Serializer):
    label = serializers.CharField(max_length=60, required=False, default="essai")
    inputs = serializers.JSONField()
    expected = serializers.JSONField(required=False, default=list)
    mocks = serializers.JSONField(required=False, default=dict)


class StartIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    samples = serializers.ListField(child=SampleIn(), required=False, max_length=MAX_SAMPLES)


class CaseIn(serializers.Serializer):
    label = serializers.CharField(max_length=60, required=False, default="cas")
    inputs = serializers.JSONField()
    expected = serializers.JSONField(required=False, default=dict)


class ActionTestIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    version = serializers.CharField(max_length=32, required=False)
    cases = serializers.ListField(child=CaseIn(), min_length=1, max_length=MAX_CASES)


class GetIn(serializers.Serializer):
    job_id = serializers.UUIDField()


def _clean_samples(graph: dict, raw: list[dict]) -> list[dict]:
    """Chaque jeu d'essai doit respecter le schéma d'entrées ; une erreur dit lequel et pourquoi (l'assistant se corrige)."""
    from ai import dryrun

    ids = {n.get("id") for n in graph.get("nodes", [])}
    out, errors = [], {}
    for i, s in enumerate(raw):
        problems = []
        if not dryrun.valid_inputs(graph.get("inputs"), s["inputs"]):
            problems.append("`inputs` ne respecte pas le schéma d'entrées du workflow (champs obligatoires, types)")
        expected = s["expected"] if isinstance(s["expected"], list) else None
        if expected is None or not all(
            isinstance(e, dict) and e.get("step") in ids and isinstance(e.get("field"), str) and "value" in e
            for e in expected
        ):
            problems.append("`expected` : liste de {step, field, value} dont `step` est une étape du workflow")
        mocks = s["mocks"]
        if not (isinstance(mocks, dict) and all(k in ids and isinstance(v, dict) for k, v in mocks.items())):
            problems.append(
                "`mocks` : {étape: {champ: valeur}} dont l'étape existe (sorties imposées à une étape simulée)"
            )
        if problems:
            errors[f"samples.{i}"] = problems
        else:
            out.append(
                {
                    "label": s["label"],
                    "inputs": s["inputs"],
                    "expected": [{"step": e["step"], "field": e["field"], "value": e["value"]} for e in expected],
                    "mocks": mocks,
                }
            )
    if errors:
        raise serializers.ValidationError(errors)
    return out


def start_dry_run(request, ws, args):
    from ai import service
    from ai.models import AiJob

    from .mcp_design import _err, _ok
    from .mcp_design_write import _need_edit

    _need_edit(request, ws)
    ser = StartIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    wf = access.workflow_or_404(request.user, ws, d["slug"], EDIT)
    graph = wf.draft_graph or {}
    if not service.policy_for(ws).dry_run:
        return _err("L'essai à blanc est désactivé pour cet espace (Administration → Assistant IA → Essai à blanc).")
    if not any(n.get("type") in ("script", "condition") for n in graph.get("nodes", [])):
        return _ok(
            {
                "applicable": False,
                "reason": "Rien à exécuter : l'essai à blanc exécute les scripts et les décisions, ce workflow n'en a pas.",
            }
        )
    samples = _clean_samples(graph, d.get("samples") or [])
    active = AiJob.objects.filter(
        workspace=ws, user=request.user, kind="dryrun", status__in=["queued", "running"]
    ).count()
    if active >= MAX_ACTIVE_JOBS:
        return _err(
            f"{active} essais sont déjà en cours ou en attente : attendez leur fin (get_dry_run) avant d'en lancer un autre."
        )
    job = AiJob.objects.create(
        workspace=ws,
        user=request.user,
        kind="dryrun",
        payload={"slug": wf.slug, "graph": graph, "samples": samples, "revision": wf.draft_revision},
    )
    record(
        "mcp.design.write",
        actor=request.user,
        workspace=ws,
        target=wf,
        tool="start_dry_run",
        external_assistant=True,
        slug=wf.slug,
        job=str(job.id),
    )
    return _ok(
        {
            "job_id": str(job.id),
            "status": "queued",
            "revision": wf.draft_revision,
            "samples": len(samples) or "automatiques (typique, limite)",
            "note": "Le worker exécute l'essai (quelques secondes à quelques minutes) : appelez get_dry_run avec ce job_id jusqu'à status = succeeded.",
        }
    )


def get_dry_run(request, ws, args):
    from ai.models import AiJob

    from .mcp_design import _ok

    if not has_perm(request.user, ws, VIEW):
        raise Http404()
    ser = GetIn(data=args)
    ser.is_valid(raise_exception=True)
    job = AiJob.objects.filter(
        pk=ser.validated_data["job_id"], workspace=ws, kind__in=["dryrun", "actiontest"], user=request.user
    ).first()
    if job is None:
        raise Http404()  # l'essai d'une autre personne n'existe pas pour celle-ci
    last = job.events[-1] if job.events else {}
    out = {
        "job_id": str(job.id),
        "status": job.status,
        "progress": job.progress,
        "phase": last.get("label", ""),
        "detail": last.get("detail", ""),
    }
    if job.status == AiJob.Status.SUCCEEDED:
        out |= {"result": job.result}
    elif job.status in (AiJob.Status.FAILED, AiJob.Status.CANCELLED):
        out |= {"error": job.error}
    else:
        out["note"] = "Pas fini : rappelez get_dry_run dans quelques secondes."
    return _ok(out)


# --- côté worker ------------------------------------------------------------------------------------------------------------
def _short(value, limit):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compact(result: dict, graph: dict, revision) -> dict:
    """Ce que l'assistant lit : verdict, problèmes, couverture, et pour chaque scénario les étapes EXÉCUTÉES (valeurs de sortie tronquées).
    Les étapes non empruntées ne sont que comptées : un workflow de 100 étapes ne doit pas noyer le modèle."""
    names = {n["id"]: n.get("name") or n["id"] for n in graph.get("nodes", [])}
    if not result.get("ran"):
        return {"ran": False, "reason": result.get("reason", "bac à sable indisponible"), "revision": revision}
    scenarios = []
    for sc in result["scenarios"]:
        steps = sc["steps"]
        shown = [
            {
                "id": s["id"],
                "name": s.get("name") or names.get(s["id"], s["id"]),
                "type": s.get("type"),
                "state": s["state"],
                "detail": _short(s.get("detail", ""), DETAIL),
            }
            | (
                {"output": {k: _short(v, OUTPUT) for k, v in (s.get("output") or {}).items()}}
                if s.get("output")
                else {}
            )
            for s in steps
            if s["state"] != "skipped"
        ]
        scenarios.append(
            {
                "label": sc["label"],
                "approvals": "accordées" if sc.get("approve", True) else "refusées",
                "outcome": sc["outcome"],
                "skipped": sum(1 for s in steps if s["state"] == "skipped"),
                "steps": shown,
                "problems": [
                    {
                        "step": p.get("node"),
                        "code": p.get("code"),
                        "message": _short(p.get("message", ""), 300),
                        "level": p.get("level"),
                    }
                    for p in sc.get("problems", [])
                ],
            }
        )
    cov = result.get("coverage", {})
    return {
        "ran": True,
        "revision": revision,
        "problems": [
            {
                "step": p.get("node"),
                "code": p.get("code"),
                "message": _short(p.get("message", ""), 300),
                "level": p.get("level"),
            }
            for p in result.get("problems", [])
        ],
        "coverage": {
            "executed": cov.get("executed"),
            "total": cov.get("total"),
            "never": [_short(x, 80) for x in cov.get("never", [])][:40],
        },
        "scenarios": scenarios,
        "legend": "state « ok » : exécuté pour de vrai dans le bac à sable (script, décision) ; « simulated » : étape simulée d'après son contrat ; « failed » : échec ; « skipped » : chemin non emprunté (compté seulement).",
    }


def run_job(job) -> dict:
    """Exécute l'essai (appelé par le worker, `ai.jobs.process`). Retourne le résultat compact."""
    from ai import dryrun, progress, service
    from runs.runners import get_runner

    payload = job.payload
    graph, samples = payload["graph"], payload.get("samples") or []
    if not samples:  # aucun jeu fourni : le typique puis la limite, comme l'assistant interne
        schema = graph.get("inputs") or {}
        samples = [
            {"label": "typique", "inputs": dryrun.sample_inputs(schema, "nominal"), "expected": [], "mechanical": True},
            {
                "label": "limite (vide, zéro, faux)",
                "inputs": dryrun.sample_inputs(schema, "limite"),
                "expected": [],
                "mechanical": True,
            },
        ]
        samples = [s for s in samples if dryrun.valid_inputs(schema, s["inputs"])]
    progress.report("dryrun", f"{len(samples)} jeu(x) d'essai ; exécution dans le bac à sable")

    def tick(i, total, label, finished=0):
        progress.report(
            "dryrun",
            f"{finished}/{total} scénario(s) terminé(s) : « {label} »",
            pct=10 + int(85 * finished / max(total, 1)),
        )

    result = dryrun.dry_run(
        graph,
        job.workspace,
        (service.RUNNER_FACTORY or get_runner)(),
        samples,
        progress=tick,
        max_steps=MAX_STEPS,
        budget=BUDGET_SECONDS,
    )
    return compact(result, graph, payload.get("revision"))


# --- test du CODE d'une action (ADR 0025, amendement) ------------------------------------------------------------------------
# Le code d'une action proposée n'est exécutable nulle part sur la plate-forme avant relecture. Une seule exception, voulue : cet essai, dans le MÊME bac
# à sable que les scripts d'un brouillon, sur une version TEMPORAIRE (jamais enregistrée) dont le réseau est coupé, sans aucun secret ni connexion, aux
# limites plafonnées. Ce n'est ni une exécution (aucune `Execution`, aucun run) ni une activation : l'action reste « proposée ».
class _Unisolated(Exception):
    pass


def _sandbox_version(version):
    from types import SimpleNamespace

    limits = {
        "timeout_seconds": min(int((version.limits or {}).get("timeout_seconds", ACTION_TIMEOUT)), ACTION_TIMEOUT),
        "memory_mb": min(int((version.limits or {}).get("memory_mb", 128)), ACTION_MEMORY_MB),
    }
    return SimpleNamespace(
        runtime=version.runtime,
        source=version.source,
        entrypoint=version.entrypoint,
        network_policy="none",
        limits=limits,
    )


def _isolated_runner():
    """Le runner du bac à sable ; refusé s'il n'isole pas (du code non relu ne doit jamais tourner sur l'hôte), sauf réglage de développement explicite."""
    from django.conf import settings

    from ai import service
    from runs.runners import SubprocessRunner, get_runner

    runner = (service.RUNNER_FACTORY or get_runner)()
    if isinstance(runner, SubprocessRunner) and not getattr(settings, "ORCH_MCP_ACTION_TEST_UNISOLATED", False):
        raise _Unisolated(
            "Le test du code d'une action exige l'exécution isolée (conteneur) : ce serveur est configuré avec un runner non isolé (ORCH_RUNNER=subprocess)."
        )
    return runner


def start_action_test(request, ws, args):
    from ai import service
    from ai.models import AiJob
    from catalog.models import Action

    from .mcp_design import _err, _ok
    from .mcp_design_write import _need_edit

    _need_edit(request, ws)
    ser = ActionTestIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    if not service.policy_for(ws).dry_run:
        return _err(
            "Les essais en bac à sable sont désactivés pour cet espace (Administration → Assistant IA → Essai à blanc)."
        )
    try:
        _isolated_runner()
    except _Unisolated as exc:
        return _err(str(exc))
    action = Action.objects.filter(workspace=ws, slug=d["slug"]).first()
    if action is None or (action.status == Action.Status.PROPOSED and not has_perm(request.user, ws, EDIT)):
        raise Http404()
    version = action.versions.filter(version=d["version"]).first() if d.get("version") else action.latest_version()
    if version is None:
        return _err("Cette action n'a pas de version à tester.")
    import jsonschema

    errors = {}
    for i, case in enumerate(d["cases"]):
        problems = [
            e.message[:160] for e in jsonschema.Draft202012Validator(version.input_schema).iter_errors(case["inputs"])
        ][:3]
        if not isinstance(case["expected"], dict):
            problems.append("`expected` : objet {champ: valeur attendue} (sous-ensemble de la sortie)")
        if problems:
            errors[f"cases.{i}"] = problems
    if errors:
        raise serializers.ValidationError(errors)
    active = AiJob.objects.filter(
        workspace=ws, user=request.user, kind__in=["dryrun", "actiontest"], status__in=["queued", "running"]
    ).count()
    if active >= MAX_ACTIVE_JOBS:
        return _err(
            f"{active} essais sont déjà en cours ou en attente : attendez leur fin (get_dry_run) avant d'en lancer un autre."
        )
    job = AiJob.objects.create(
        workspace=ws,
        user=request.user,
        kind="actiontest",
        payload={
            "slug": action.slug,
            "version_id": version.pk,
            "cases": [{"label": c["label"], "inputs": c["inputs"], "expected": c["expected"]} for c in d["cases"]],
        },
    )
    record(
        "mcp.design.write",
        actor=request.user,
        workspace=ws,
        target=action,
        tool="start_action_test",
        external_assistant=True,
        slug=action.slug,
        version=version.version,
        job=str(job.id),
    )
    return _ok(
        {
            "job_id": str(job.id),
            "status": "queued",
            "action": action.slug,
            "version": version.version,
            "cases": len(d["cases"]),
            "note": "Le worker exécute le code dans le bac à sable (sans réseau ni secret). Appelez get_dry_run avec ce job_id jusqu'à status = succeeded.",
        }
    )


def run_action_test(job) -> dict:
    """Exécute les cas (appelé par le worker). Une version temporaire, réseau coupé, sans variable d'environnement, limites plafonnées."""
    import time

    import jsonschema

    from ai import progress
    from catalog.models import ActionVersion

    version = ActionVersion.objects.select_related("action").get(pk=job.payload["version_id"])
    sandbox = _sandbox_version(version)
    runner = _isolated_runner()
    cases = []
    for i, case in enumerate(job.payload["cases"], start=1):
        progress.report(
            "actiontest",
            f"cas {i}/{len(job.payload['cases'])} : « {case['label']} »",
            pct=10 + int(85 * (i - 1) / len(job.payload["cases"])),
        )
        started = time.monotonic()
        outcome = runner.run(
            sandbox, case["inputs"], sandbox.limits["timeout_seconds"], None, None
        )  # env = None : aucun secret, aucune connexion
        problems = []
        out = outcome.result if outcome.ok else None
        if not outcome.ok:
            problems.append(
                {
                    "code": (outcome.error or {}).get("code", "ERREUR"),
                    "message": _short((outcome.error or {}).get("message", "échec"), 300),
                }
            )
        else:
            for e in list(jsonschema.Draft202012Validator(version.output_schema).iter_errors(out))[:3]:
                problems.append(
                    {"code": "OUTPUT_INVALID", "message": f"La sortie ne respecte pas le contrat : {e.message[:200]}"}
                )
            for key, want in (case["expected"] or {}).items():
                got = (out or {}).get(key) if isinstance(out, dict) else None
                if got != want:
                    problems.append(
                        {
                            "code": "EXPECTED",
                            "message": f"« {key} » : attendu {_short(want, 80)}, obtenu {_short(got, 80)}",
                        }
                    )
        cases.append(
            {
                "label": case["label"],
                "ok": outcome.ok and not problems,
                "seconds": round(time.monotonic() - started, 2),
                "output": _short(out, 600) if outcome.ok else None,
                "problems": problems,
                "logs": _short((outcome.logs or "").strip()[-500:], 500),
            }
        )
    return {
        "ran": True,
        "action": version.action.slug,
        "version": version.version,
        "cases": cases,
        "legend": "Code exécuté dans le bac à sable : réseau coupé, aucun secret ni connexion, limites plafonnées. L'action reste « proposée » : ce test ne la rend ni active ni utilisable.",
    }
