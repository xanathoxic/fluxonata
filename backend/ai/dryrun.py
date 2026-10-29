"""Dry run (« essai à blanc ») of a draft workflow, before a person sees it.

What runs for real, in the same isolated sandbox as a production script step (no network, no files, no secrets, time and
memory limits): the SCRIPT steps and the DECISIONS (conditions), on sample inputs, following the same activation rules as
the engine (a decision opens one branch, an error exit only fires on failure, a join continues when one flow arrived).
What is only SIMULATED, because it would act on the outside world or wait for a person: actions, calls to other workflows,
AI steps, approvals and waits. Their outputs are built from their declared contract (types, field names), so the logic
around them is exercised without calling anything.

It finds what static analysis cannot: a script that crashes on a realistic value or an empty list, an output of the wrong
type, a decision reading a field that does not exist, a step read on a path where it never ran. It does NOT prove that the
workflow is right: only that it holds on the samples given, and that the results the request states in plain words come
out. Nothing here is published, stored or sent anywhere; the web process never calls it (the generation job does)."""

import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import jsonschema

from runs.builtin import get_script_version
from runs.engine import _script_output_problem, script_payload
from workflows.expressions import ExpressionError, evaluate, resolve
from workflows.validation import (
    case_ids,
    resolve_action_version,
    resolve_skill_version,
    resolve_workflow_version,
    topo_order,
)

MAX_SCENARIOS = 12
MAX_BRANCH_SCENARIOS = 3
MAX_FAILURE_SCENARIOS = 2
MAX_PARALLEL = 3
SCRIPT_TIMEOUT = 20  # seconds per script: an essay must stay short
TOTAL_BUDGET = 150  # seconds for the whole dry run
MAX_STEPS = 80
STATUS_NAMES = {"etat", "status", "statut", "state", "resultat", "result"}
INFRA_CODES = {"RUNNER_ERROR", "PROVIDER_UNREACHABLE"}


# --------------------------------------------------------------------------------------------------- sample values
def synth(schema, variant: str = "nominal", name: str = "", depth: int = 0):
    """A value that satisfies a JSON schema: « nominal » is typical, « limite » is empty/zero/false."""
    if not isinstance(schema, dict) or depth > 4:
        return "exemple"
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    nominal = variant == "nominal"
    if schema.get("enum"):
        return schema["enum"][0]
    if nominal and "default" in schema:
        return schema["default"]
    if kind == "integer" or (kind is None and name.startswith("nb_")):
        value = max(3 if nominal else 0, schema.get("minimum", 0 if not nominal else 3))
        return min(value, schema.get("maximum", value))
    if kind == "number":
        value = max(10.5 if nominal else 0, schema.get("minimum", 0 if not nominal else 10.5))
        return min(value, schema.get("maximum", value))
    if kind == "boolean":
        return nominal
    if kind == "array":
        items = schema.get("items")
        if not nominal:
            return []
        return [synth(items, variant, name, depth + 1)] * 2 if items else ["exemple-1", "exemple-2"]
    if kind == "object":
        props = schema.get("properties") or {}
        return {k: synth(v, variant, k, depth + 1) for k, v in props.items()}
    if name in STATUS_NAMES:
        return "ok"
    if name.endswith("_ms") or name in ("total", "count", "nb") or name.startswith("nb_"):
        return 1 if nominal else 0
    return "exemple" if nominal else ""


def sample_inputs(schema: dict, variant: str) -> dict:
    props = (schema or {}).get("properties") or {}
    return {k: synth(v, variant, k) for k, v in props.items()}


def valid_inputs(schema: dict, value) -> bool:
    try:
        jsonschema.validate(value, schema or {"type": "object"})
        return isinstance(value, dict)
    except jsonschema.ValidationError:
        return False


# ------------------------------------------------------------------------------------------------------ one scenario
def _short(value, limit: int = 80) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _crash(outcome) -> tuple[str, str, int]:
    """(what went wrong, where) from a failed script run: the exception line and the line of the script."""
    message = str((outcome.error or {}).get("message") or "échec").strip().splitlines()[0][:200]
    lines = re.findall(r'File "<script>", line (\d+)', outcome.logs or "")
    return message, (f" (ligne {lines[-1]})" if lines else ""), (int(lines[-1]) if lines else 0)


def _deliberate(script: str, line: int) -> bool:
    """The script stopped on a `raise` of its own (a validation that rejects bad input): an outcome it was written for, not a bug."""
    rows = str(script).splitlines()
    return 0 < line <= len(rows) and rows[line - 1].strip().startswith("raise ")


def _contract_outputs(node: dict, workspace) -> dict:
    """Plausible outputs of a step that is only simulated, built from its declared contract."""
    t = node["type"]
    if t == "action":
        _, v = resolve_action_version(workspace, node.get("action"), node.get("version"))
        schema = (v.output_schema if v else None) or {}
        return synth({"type": "object", **schema}) if schema.get("properties") else {}
    if t == "ai":
        schema = node.get("output_schema") or {}
        if node.get("skill"):
            _, sv = resolve_skill_version(workspace, node["skill"], node.get("skill_version"))
            schema = (sv.output_schema if sv else None) or {}
        return synth({"type": "object", **schema}) if schema.get("properties") else {}
    if t == "subworkflow":
        _, v = resolve_workflow_version(workspace, node.get("workflow"), node.get("version"))
        return {k: synth({}, "nominal", k) for k in ((v.graph or {}).get("outputs") or {})} if v else {}
    return {}


class _Fatal(Exception):
    pass


def prepare(graph: dict, workspace, cache: dict | None = None) -> dict:
    """Everything a scenario needs from the database, read ONCE in the calling thread (scenarios run in threads and
    touch nothing but the runner): the script wrappers and the contract outputs of the simulated steps."""
    nodes = graph.get("nodes", [])
    return {
        "versions": {
            lang: get_script_version(workspace, lang)
            for lang in {n["language"] for n in nodes if n["type"] == "script"}
        },
        "contracts": {
            n["id"]: _contract_outputs(n, workspace) for n in nodes if n["type"] in ("action", "ai", "subworkflow")
        },
        "loops": {
            n["id"]: _contract_outputs(
                {"type": "subworkflow", "workflow": n.get("workflow"), "version": n.get("version")}, workspace
            )
            for n in nodes
            if n["type"] == "foreach"
        },
        "cache": cache if cache is not None else {},  # identical script runs (same code, same values) are done once
        "lock": threading.Lock(),
    }


def _run_script(runner, helpers: dict, node: dict, payload: dict):
    """One sandboxed script run; the same code on the same values is run once (a refused-approval scenario repeats the
    scripts of the nominal one, and starting a container is the slow part)."""
    timeout = min(int(node.get("timeout_seconds") or 60), SCRIPT_TIMEOUT)
    key = (node["language"], json.dumps(payload, sort_keys=True, default=str), timeout)
    with helpers["lock"]:
        pending = helpers["cache"].get(key)
        mine = pending is None
        if mine:
            pending = helpers["cache"][key] = Future()
    if not mine:  # another scenario is running (or has run) exactly this: wait for its answer
        return pending.result()
    try:
        res = runner.run(helpers["versions"][node["language"]], payload, timeout)
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    pending.set_result(res)
    return res


def run_scenario(  # noqa: C901
    graph: dict,
    workspace,
    runner,
    *,
    label: str,
    inputs: dict,
    approve: bool,
    strict: bool,
    deadline: float,
    expected: list | None = None,
    helpers: dict | None = None,
    fail: str | set | None = None,
    force: tuple | dict | None = None,
    mechanical: bool = False,
    pick: dict | None = None,
) -> dict:
    """Walk the graph with the engine's activation rules. `strict`: problems are errors (the nominal scenario) rather
    than warnings (edge cases, which may legitimately fail on empty data)."""
    helpers = helpers or prepare(graph, workspace)
    fails = {fail} if isinstance(fail, str) else set(fail or ())
    forced = {force[0]: {force[1]: force[2]}} if isinstance(force, tuple) else dict(force or {})
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    incoming = {i: [e for e in graph.get("edges", []) if e["to"] == i] for i in nodes}
    outgoing = {i: [e for e in graph.get("edges", []) if e["from"] == i] for i in nodes}
    order = topo_order(graph.get("nodes", []), graph.get("edges", [])) or list(nodes)
    ctx = {
        "inputs": inputs,
        "vars": {v["name"]: v.get("value") for v in graph.get("variables") or []},
        "run": {"id": "essai", "started_at": "2026-01-01T00:00:00+00:00", "environment": "dev"},
        "steps": {},
    }
    state: dict[str, str] = {}
    decided: dict[str, bool] = {}
    chosen: dict[str, str] = {}  # multi-way choices: the case taken
    steps: list[dict] = []
    problems: list[dict] = []
    level = "error" if strict else "warning"

    def problem(nid, code, message):
        problems.append(
            {
                "node": nid,
                "level": level,
                "code": code,
                "message": f"{message} (essai « {label} »)",
                "scenario": label,
                "mechanical": mechanical,
            }
        )

    def record(nid, status, detail="", output=None):
        steps.append(
            {
                "id": nid,
                "name": nodes[nid].get("name") or nid,
                "type": nodes[nid]["type"],
                "state": status,
                "detail": detail,
                **({"output": output} if output is not None else {}),
            }
        )

    def lookup_failure(nid, exc: ExpressionError) -> str:
        skipped = [s for s in re.findall(r"steps\.(\w+)", str(exc)) if state.get(s) == "skipped"]
        gone = (
            f" : l'étape « {nodes[skipped[0]].get('name') or skipped[0]} » n'a pas été exécutée sur ce chemin"
            if skipped
            else ""
        )
        return f"{exc}{gone}"

    outcome = {"status": "succeeded", "message": ""}
    executed = 0
    try:
        for nid in order:
            node, inc = nodes[nid], incoming[nid]
            if time.monotonic() > deadline or executed >= MAX_STEPS:
                outcome = {"status": "stopped", "message": "Essai interrompu (durée ou nombre d'étapes maximal)."}
                break
            active = []
            for e in inc:
                src, lab = state.get(e["from"]), e.get("label") or "next"
                if src == "ok":
                    if nodes[e["from"]]["type"] == "condition":
                        ok = lab == str(decided[e["from"]]).lower()
                    elif nodes[e["from"]]["type"] == "switch":
                        ok = lab == chosen[e["from"]]
                    else:
                        ok = lab == "next"
                elif src == "failed":
                    ok = lab == "error"
                else:
                    ok = False
                active.append(ok)
            if inc and not any(active):
                state[nid] = "skipped"
                record(nid, "skipped", "Chemin non emprunté dans cet essai.")
                continue
            executed += 1
            t = node["type"]
            try:
                if nid in fails and t in ("action", "subworkflow", "ai", "foreach"):
                    state[nid] = "failed"  # a failure we decided: the error exit is what this scenario is about
                    record(nid, "simulated", "Échec simulé : on suit la sortie d'erreur.")
                    if not any(e.get("label") == "error" for e in outgoing[nid]):
                        raise _Fatal("étape en échec")
                elif t == "script":
                    try:
                        payload = script_payload(node, ctx)
                    except ExpressionError as exc:
                        problem(
                            nid,
                            "dryrun_expression",
                            f"Une liaison du script « {node.get('name') or nid} » ne peut pas être lue : {lookup_failure(nid, exc)}",
                        )
                        raise _Fatal(f"liaison illisible : {exc}") from None
                    try:
                        res = _run_script(runner, helpers, node, payload)
                    except Exception as exc:  # noqa: BLE001  (the runner itself is unavailable: not the script's fault)
                        raise _Fatal(f"__infra__:{exc}") from None
                    if (res.error or {}).get("code") in INFRA_CODES:
                        raise _Fatal(f"__infra__:{res.error.get('message')}")
                    if not res.ok:
                        why, where, at = _crash(res)
                        if not _deliberate(node.get("script", ""), at):
                            problem(
                                nid,
                                "dryrun_script_error",
                                f"Le script « {node.get('name') or nid} » plante{where} : {why}",
                            )
                        state[nid] = "failed"
                        record(nid, "failed", f"{why}{where}")
                        if not any(e.get("label") == "error" for e in outgoing[nid]):
                            raise _Fatal(f"script en échec : {why}")
                        continue
                    wrong = _script_output_problem(node, res.result)
                    if wrong:
                        problem(nid, "dryrun_output", f"Script « {node.get('name') or nid} » : {wrong}")
                        state[nid] = "failed"
                        record(nid, "failed", wrong)
                        if not any(e.get("label") == "error" for e in outgoing[nid]):
                            raise _Fatal(wrong)
                        continue
                    ctx["steps"][nid] = {"output": res.result}
                    state[nid] = "ok"
                    record(
                        nid, "ok", "Exécuté dans le bac à sable.", {k: _short(v) for k, v in (res.result or {}).items()}
                    )
                elif t == "condition":
                    expr = re.sub(r"\{\{\s*(.*?)\s*\}\}", r"\1", str(node.get("expr") or "")).strip()
                    try:
                        decided[nid] = bool(evaluate(expr, ctx))
                    except ExpressionError as exc:
                        problem(
                            nid,
                            "dryrun_expression",
                            f"La décision « {node.get('name') or nid} » ne peut pas être évaluée : {lookup_failure(nid, exc)}",
                        )
                        state[nid] = "failed"
                        record(nid, "failed", str(exc))
                        raise _Fatal(f"décision illisible : {exc}") from None
                    ctx["steps"][nid] = {"output": {"result": decided[nid]}}
                    state[nid] = "ok"
                    record(nid, "ok", "Oui" if decided[nid] else "Non")
                elif t == "switch":
                    forced_case = (pick or {}).get(nid)
                    try:
                        chosen[nid] = forced_case or next(
                            (
                                c["id"]
                                for c in node.get("cases") or []
                                if evaluate(
                                    re.sub(r"\{\{\s*(.*?)\s*\}\}", r"\1", str(c.get("expr") or "")).strip(), ctx
                                )
                            ),
                            "default",
                        )
                    except ExpressionError as exc:
                        problem(
                            nid,
                            "dryrun_expression",
                            f"Le choix « {node.get('name') or nid} » ne peut pas être évalué : {lookup_failure(nid, exc)}",
                        )
                        state[nid] = "failed"
                        record(nid, "failed", str(exc))
                        raise _Fatal(f"choix illisible : {exc}") from None
                    ctx["steps"][nid] = {"output": {"result": chosen[nid]}}
                    state[nid] = "ok"
                    record(nid, "ok", f"Cas « {chosen[nid]} »" + (" (imposé par l'essai)" if forced_case else ""))
                elif t == "throw":
                    state[nid] = "failed"
                    record(nid, "failed", str(node.get("message") or "Exception"))
                    raise _Fatal(f"exception « {node.get('message') or nid} »")
                elif t == "approval":
                    if approve:
                        ctx["steps"][nid] = {"output": {"approved": True, "decided_by": "essai", "comment": ""}}
                        state[nid] = "ok"
                        record(nid, "simulated", "Approbation simulée : accordée.")
                    else:
                        state[nid] = "failed"
                        record(nid, "simulated", "Approbation simulée : refusée.")
                        if not any(e.get("label") == "error" for e in outgoing[nid]):
                            raise _Fatal("approbation refusée")
                elif t == "foreach":
                    try:
                        listed = evaluate(
                            re.sub(r"\{\{\s*(.*?)\s*\}\}", r"\1", str(node.get("items") or "")).strip(), ctx
                        )
                        resolve(node.get("inputs") or {}, {**ctx, "inputs": ctx["inputs"]})
                    except ExpressionError as exc:
                        problem(
                            nid,
                            "dryrun_expression",
                            f"La boucle « {node.get('name') or nid} » ne peut pas lire sa liste : {lookup_failure(nid, exc)}",
                        )
                        state[nid] = "failed"
                        record(nid, "failed", str(exc))
                        raise _Fatal(f"boucle illisible : {exc}") from None
                    if not isinstance(listed, list):
                        problem(
                            nid,
                            "dryrun_expression",
                            f"La boucle « {node.get('name') or nid} » doit parcourir une liste, elle reçoit {type(listed).__name__}.",
                        )
                        state[nid] = "failed"
                        record(nid, "failed", "Ce n'est pas une liste.")
                        raise _Fatal("boucle sur autre chose qu'une liste")
                    child = helpers["loops"].get(nid, {})
                    ctx["steps"][nid] = {"output": {"count": len(listed), "results": [dict(child) for _ in listed[:3]]}}
                    state[nid] = "ok"
                    record(
                        nid,
                        "simulated",
                        f"Boucle simulée : {len(listed)} élément(s), {min(len(listed), 3)} résultat(s) d'exemple.",
                    )
                elif t in ("action", "subworkflow", "ai"):
                    try:
                        resolve(node.get("inputs") or {}, ctx)
                        if t == "ai" and node.get("prompt"):
                            resolve(node["prompt"], ctx)
                    except ExpressionError as exc:
                        problem(
                            nid,
                            "dryrun_expression",
                            f"Une valeur de l'étape « {node.get('name') or nid} » ne peut pas être lue : {lookup_failure(nid, exc)}",
                        )
                        state[nid] = "failed"
                        record(nid, "failed", str(exc))
                        if not any(e.get("label") == "error" for e in outgoing[nid]):
                            raise _Fatal(f"valeur illisible : {exc}") from None
                        continue
                    ctx["steps"][nid] = {
                        "output": {
                            **helpers["contracts"].get(nid, {}),
                            **forced.get(nid, {}),
                        }
                    }
                    state[nid] = "ok"
                    record(nid, "simulated", "Simulée : sortie construite d'après son contrat.")
                elif t == "transform":
                    from workflows import transforms

                    try:
                        produced = transforms.apply(node, ctx)
                        note = "Transformation exécutée sur les données d'essai."
                    except transforms.TransformError:
                        produced = transforms.output_fields(node)
                        produced = {
                            k: ([] if v.get("type") == "array" else 0 if v.get("type") == "integer" else "")
                            for k, v in produced.items()
                        }
                        note = "Transformation simulée (données d'essai inexploitables : sortie vide)."
                    ctx["steps"][nid] = {"output": produced}
                    state[nid] = "ok"
                    record(nid, "simulated", note)
                elif t == "sftp":
                    ctx["steps"][nid] = {
                        "output": {
                            "operation": node.get("operation", "list"),
                            "path": "/",
                            "entries": [],
                            "count": 0,
                            "content": "",
                            "simulated": True,
                        }
                    }
                    state[nid] = "ok"
                    record(nid, "simulated", "Fichier SFTP simulé : rien n'est envoyé.")
                elif t == "ldap":
                    ctx["steps"][nid] = {"output": {"rows": [], "count": 0, "truncated": False, "simulated": True}}
                    state[nid] = "ok"
                    record(nid, "simulated", "Recherche d'annuaire simulée : rien n'est envoyé.")
                elif t == "while":
                    ctx["steps"][nid] = {"output": {"iterations": 1, "results": [], "last": {}, "stopped": "condition"}}
                    state[nid] = "ok"
                    record(nid, "simulated", "Boucle « tant que » simulée : une seule répétition.")
                elif t == "agent":
                    ctx["steps"][nid] = {
                        "output": {
                            "answer": "réponse d'exemple",
                            "iterations": 1,
                            "steps": [],
                            "stopped": "done",
                            "simulated": True,
                        }
                    }
                    state[nid] = "ok"
                    record(nid, "simulated", "Agent simulé : le modèle n'est pas interrogé dans l'essai à blanc.")
                elif t == "sql":
                    ctx["steps"][nid] = {
                        "output": {"rows": [], "count": 0, "columns": [], "truncated": False, "simulated": True}
                    }
                    state[nid] = "ok"
                    record(nid, "simulated", "Requête SQL simulée : rien n'est envoyé.")
                elif t == "http":
                    ctx["steps"][nid] = {
                        "output": {"status": 200, "ok": True, "headers": {}, "body": {}, "simulated": True}
                    }
                    state[nid] = "ok"
                    record(nid, "simulated", "Requête HTTP simulée : rien n'est envoyé.")
                else:  # wait, join
                    ctx["steps"][nid] = {
                        "output": {"resumed": True, "payload": {}} if node.get("resume") == "webhook" else {}
                    }
                    state[nid] = "ok"
                    record(
                        nid,
                        "simulated" if t == "wait" else "ok",
                        "Attente simulée (non attendue)." if t == "wait" else "",
                    )
            except _Fatal:
                raise
    except _Fatal as stop:
        text = str(stop)
        outcome = {
            "status": "infra" if text.startswith("__infra__") else "failed",
            "message": text.removeprefix("__infra__:"),
        }
    if not approve and outcome["status"] == "failed" and outcome["message"] == "approbation refusée":
        for x in [x for x in steps if x["type"] == "approval" and x["state"] == "simulated"][
            -1:
        ]:  # the one that ended it
            problems.append(
                {
                    "node": x["id"],
                    "level": "warning",
                    "code": "dryrun_refusal",
                    "scenario": label,
                    "message": f"Si « {x['name']} » est refusée, le workflow s'arrête sans message ni notification : "
                    "aucune sortie d'erreur n'est reliée à cette étape (ajoutez ce qui doit se passer en cas de refus) "
                    f"(essai « {label} »).",
                }
            )
    for want in expected or []:
        out = (ctx["steps"].get(want.get("step")) or {}).get("output") or {}
        if want.get("field") in out and not _same(out[want["field"]], want.get("value")):
            node = nodes.get(want["step"], {})
            problems.append(
                {
                    "node": want["step"],
                    "level": "warning",
                    "code": "dryrun_unexpected",
                    "scenario": label,
                    "message": f"Résultat différent de l'attendu pour « {node.get('name') or want['step']} » ({want['field']}) : "
                    f"attendu {_short(want.get('value'))}, obtenu {_short(out[want['field']])} (essai « {label} »). À vérifier : "
                    "l'attendu vient de la demande, l'obtenu de l'exécution du script.",
                }
            )
    return {
        "label": label,
        "inputs": inputs,
        "approve": approve,
        "outcome": outcome,
        "steps": steps,
        "problems": problems,
    }


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-6 * max(1.0, abs(b))
    return a == b


# ------------------------------------------------------------------------------------------------- the whole dry run
AWAITED = re.compile(
    r"""^steps\.([A-Za-z0-9_]+)\.output\.([A-Za-z0-9_]+)\s*==\s*(?:'([^']*)'|"([^"]*)"|(true|false)|(-?\d+(?:\.\d+)?))$"""
)


def _awaited_values(graph: dict) -> list[tuple]:
    """(step, field, value, text shown) for every decision that waits for a given value from a SIMULATED step: simulated
    outputs are typical values, so without this the branch such a decision opens is never walked."""
    simulated = {n["id"] for n in graph.get("nodes", []) if n["type"] in ("action", "subworkflow", "ai")}
    found, seen = [], set()
    for n in graph.get("nodes", []):
        if n["type"] != "condition":
            continue
        expr = re.sub(r"\{\{\s*(.*?)\s*\}\}", r"\1", str(n.get("expr") or "")).strip()
        m = AWAITED.match(expr)
        if not m or m.group(1) not in simulated or (m.group(1), m.group(2), m.group(0)) in seen:
            continue
        seen.add((m.group(1), m.group(2), m.group(0)))
        text, boolean, number = (m.group(3) if m.group(3) is not None else m.group(4)), m.group(5), m.group(6)
        value = (
            text
            if text is not None
            else (boolean == "true")
            if boolean
            else float(number)
            if "." in number
            else int(number)
        )
        found.append((m.group(1), m.group(2), value, f"« {text} »" if text is not None else (boolean or number)))
    return found


def dry_run(graph: dict, workspace, runner, samples: list[dict], progress=None, cache: dict | None = None) -> dict:
    """Run the scenarios: each sample (label, inputs, expected) with approvals granted, plus one with approvals refused
    when the workflow has any. Returns {scenarios, problems, coverage, ran}."""
    deadline = time.monotonic() + TOTAL_BUDGET
    plan = [(s, True, None, None) for s in samples[:3]]
    if any(n["type"] == "approval" for n in graph.get("nodes", [])):  # each sample again, approvals refused
        plan += [
            ({**x, "label": f"{x['label']}, approbation refusée", "expected": []}, False, None, None)
            for x in samples[:3]
        ]
    # the error exits: nothing reaches them on good data, so each simulated step that has one is failed once
    names = {n["id"]: n.get("name") or n["id"] for n in graph.get("nodes", [])}
    failing = [
        n["id"]
        for n in graph.get("nodes", [])
        if n["type"] in ("action", "subworkflow", "foreach", "ai")
        and any(e["from"] == n["id"] and e.get("label") == "error" for e in graph.get("edges", []))
    ][:MAX_FAILURE_SCENARIOS]
    awaited = _awaited_values(graph)
    reach_all = {}  # every value a decision waits for, applied together: the branches that hold the steps worth failing
    for nid, field, value, _ in awaited:
        reach_all.setdefault(nid, {}).setdefault(field, value)
    if samples:
        plan += [
            ({**samples[0], "label": f"« {names[i]} » en échec (simulé)", "expected": []}, True, i, reach_all or None)
            for i in failing
        ]
        every = [
            n["id"]
            for n in graph.get("nodes", [])
            if n["type"] in ("action", "subworkflow", "foreach", "ai")
            and any(e["from"] == n["id"] and e.get("label") == "error" for e in graph.get("edges", []))
        ]
        if len(every) >= 2:  # a chain of recoveries (retry after retry) is only walked when each link fails in turn
            plan.append(
                (
                    {**samples[0], "label": "chaque étape qui a une sortie d'erreur échoue (simulé)", "expected": []},
                    True,
                    set(every),
                    reach_all or None,
                )
            )
    if (
        samples
    ):  # the branches that depend on what a simulated step returns: one scenario per value a decision waits for
        plan += [
            (
                {**samples[0], "label": f"« {names[nid]} » renvoie {field} = {shown} (simulé)", "expected": []},
                True,
                None,
                (nid, field, value),
            )
            for nid, field, value, shown in awaited[:MAX_BRANCH_SCENARIOS]
        ]
    if samples:  # every case of a multi-way choice is walked once, whatever the samples happen to match
        for n in graph.get("nodes", []):
            if n["type"] == "switch":
                plan += [
                    (
                        {**samples[0], "label": f"« {names[n['id']]} » prend le cas « {c} » (simulé)", "expected": []},
                        True,
                        None,
                        {"__pick__": {n["id"]: c}},
                    )
                    for c in [*case_ids(n), "default"]
                    if any(e["from"] == n["id"] and e.get("label") == c for e in graph.get("edges", []))
                ][:MAX_BRANCH_SCENARIOS]
    plan = plan[:MAX_SCENARIOS]
    helpers = prepare(graph, workspace, cache)  # database reads happen here, once; scenarios only use the runner

    def one(i):
        sample, approve, fail, force = plan[i]
        picked = force.get("__pick__") if isinstance(force, dict) else None
        force = None if picked else force
        return run_scenario(
            graph,
            workspace,
            runner,
            label=sample["label"],
            inputs=sample["inputs"],
            approve=approve,
            strict=(i == 0),
            deadline=deadline,
            expected=sample.get("expected"),
            helpers=helpers,
            fail=fail,
            force=force,
            mechanical=bool(sample.get("mechanical")),
            pick=picked,
        )

    # The scenarios are independent: they run side by side (starting a container is slow, waiting for each in turn is not).
    scenarios, problems = [None] * len(plan), []
    with ThreadPoolExecutor(max_workers=min(len(plan), MAX_PARALLEL) or 1) as pool:
        futures = {pool.submit(one, i): i for i in range(len(plan))}
        done = 0
        for future in as_completed(futures):
            i = futures[future]
            scenarios[i] = future.result()
            done += 1
            if progress:
                progress(done - 1, len(plan), plan[i][0]["label"], done)
    for sc in scenarios:
        if sc["outcome"]["status"] == "infra":  # the sandbox is not available here: no verdict, say so
            return {
                "scenarios": scenarios,
                "problems": [],
                "coverage": {},
                "ran": False,
                "reason": sc["outcome"]["message"],
            }
        problems += sc["problems"]
    seen, unique = set(), []
    for p in problems:
        key = (p["node"], p["code"], p["message"].split(" (essai")[0])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    ran = {s["id"] for sc in scenarios for s in sc["steps"] if s["state"] != "skipped"}
    names = {n["id"]: n.get("name") or n["id"] for n in graph.get("nodes", [])}
    return {
        "scenarios": scenarios,
        "problems": unique,
        "ran": True,
        "coverage": {
            "executed": len(ran),
            "total": len(names),
            "never": sorted(names[i] for i in names if i not in ran),
        },
    }


def summary(result: dict) -> dict:
    """What is kept in the check report: compact, no input values."""
    return {
        "ran": result.get("ran", False),
        "reason": result.get("reason", ""),
        "scenarios": [
            {
                "label": s["label"],
                "outcome": s["outcome"],
                "executed": sum(1 for x in s["steps"] if x["state"] != "skipped"),
                "steps": [
                    {"id": x["id"], "name": x["name"], "state": x["state"], "detail": x["detail"][:120]}
                    for x in s["steps"]
                ],
            }
            for s in result.get("scenarios", [])
        ],
        "coverage": result.get("coverage", {}),
        "problems": len(result.get("problems", [])),
    }
