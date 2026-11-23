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
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import jsonschema

from runs import ssh_node
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
log = logging.getLogger("fluxonata.ai.dryrun")
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


def valid_inputs(schema: dict | None, value) -> bool:
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
        "ssh": ssh_node.endpoints_of(workspace) if any(n["type"] == "ssh" for n in nodes) else {},
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


def run_scenario(
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
    max_steps: int = MAX_STEPS,
) -> dict:
    """Walk the graph with the engine's activation rules. `strict`: problems are errors (the nominal scenario) rather
    than warnings (edge cases, which may legitimately fail on empty data)."""
    walk = _Walk(
        graph,
        runner,
        helpers or prepare(graph, workspace),
        label=label,
        inputs=inputs,
        approve=approve,
        strict=strict,
        mechanical=mechanical,
        fail=fail,
        force=force,
        pick=pick,
    )
    outcome = walk.run(deadline, max_steps)
    if not approve and outcome["status"] == "failed" and outcome["message"] == "approbation refusée":
        walk.problems += _refusal_problems(walk.steps, label)
    walk.problems += _expectation_problems(expected or [], walk.ctx, walk.nodes, label)
    return {
        "label": label,
        "inputs": inputs,
        "approve": approve,
        "outcome": outcome,
        "steps": walk.steps,
        "problems": walk.problems,
    }


def _refusal_problems(steps: list[dict], label: str) -> list[dict]:
    """An approval refused with no error exit ends the run silently: say so for the one that ended it."""
    return [
        {
            "node": x["id"],
            "level": "warning",
            "code": "dryrun_refusal",
            "scenario": label,
            "message": f"Si « {x['name']} » est refusée, le workflow s'arrête sans message ni notification : "
            "aucune sortie d'erreur n'est reliée à cette étape (ajoutez ce qui doit se passer en cas de refus) "
            f"(essai « {label} »).",
        }
        for x in [x for x in steps if x["type"] == "approval" and x["state"] == "simulated"][-1:]
    ]


def _expectation_problems(expected: list, ctx: dict, nodes: dict, label: str) -> list[dict]:
    """The results the request states, compared with what the steps produced."""
    out = []
    for want in expected:
        got = (ctx["steps"].get(want.get("step")) or {}).get("output") or {}
        if want.get("field") in got and not _same(got[want["field"]], want.get("value")):
            node = nodes.get(want["step"], {})
            out.append(
                {
                    "node": want["step"],
                    "level": "warning",
                    "code": "dryrun_unexpected",
                    "scenario": label,
                    "message": f"Résultat différent de l'attendu pour « {node.get('name') or want['step']} » ({want['field']}) : "
                    f"attendu {_short(want.get('value'))}, obtenu {_short(got[want['field']])} (essai « {label} »). À vérifier : "
                    "l'attendu vient de la demande, l'obtenu de l'exécution du script.",
                }
            )
    return out


def _sftp_output(node: dict) -> dict:
    return {
        "operation": node.get("operation", "list"),
        "path": "/",
        "entries": [],
        "count": 0,
        "content": "",
        "simulated": True,
    }


def _cache_output(node: dict) -> dict:
    """Étape « Cache » simulée : le cache réel n'est ni lu ni écrit (ni reconstruit) dans un essai à blanc."""
    key = str(node.get("key") or "")
    outputs: dict[str, dict] = {
        "read": {"found": False, "value": None, "version": 0, "age_seconds": None, "key": key, "rebuilt": False},
        "write": {"written": True, "key": key, "version": 1, "size_bytes": 0, "expires_at": None},
        "delete": {"deleted": False, "key": key},
    }
    return outputs.get(str(node.get("operation")), {})


# kinds that are only simulated: (output, message) — the output may depend on the step
_FIXED_STEPS = {
    "cache": (_cache_output, "Cache simulé : rien n'est lu, écrit ni supprimé dans le cache réel."),
    "smtp": (
        {"sent": False, "simulated": True, "recipients": 1, "message_id": None},
        "E-mail simulé : rien n'est envoyé.",
    ),
    "sftp": (_sftp_output, "Fichier SFTP simulé : rien n'est envoyé."),
    "ldap": (
        {"rows": [], "count": 0, "truncated": False, "simulated": True},
        "Recherche d'annuaire simulée : rien n'est envoyé.",
    ),
    "while": (
        {"iterations": 1, "results": [], "last": {}, "stopped": "condition"},
        "Boucle « tant que » simulée : une seule répétition.",
    ),
    "agent": (
        {"answer": "réponse d'exemple", "iterations": 1, "steps": [], "stopped": "done", "simulated": True},
        "Agent simulé : le modèle n'est pas interrogé dans l'essai à blanc.",
    ),
    "sql": (
        {"rows": [], "count": 0, "columns": [], "truncated": False, "simulated": True},
        "Requête SQL simulée : rien n'est envoyé.",
    ),
    "http": (
        {"status": 200, "ok": True, "headers": {}, "body": {}, "simulated": True},
        "Requête HTTP simulée : rien n'est envoyé.",
    ),
}


def _placeholders(text) -> str:
    """An expression written with or without its {{ }} braces, as the bare expression."""
    return re.sub(r"\{\{\s*(.*?)\s*\}\}", r"\1", str(text or "")).strip()


class _Walk:
    """One scenario: the graph walked in order with the engine's activation rules, each step run or simulated."""

    def __init__(self, graph, runner, helpers, *, label, inputs, approve, strict, mechanical, fail, force, pick):
        self.runner, self.helpers, self.label, self.approve = runner, helpers, label, approve
        self.mechanical, self.pick = mechanical, pick
        self.fails = {fail} if isinstance(fail, str) else set(fail or ())
        self.forced = {force[0]: {force[1]: force[2]}} if isinstance(force, tuple) else dict(force or {})
        self.nodes = {n["id"]: n for n in graph.get("nodes", [])}
        self.incoming = {i: [e for e in graph.get("edges", []) if e["to"] == i] for i in self.nodes}
        self.outgoing = {i: [e for e in graph.get("edges", []) if e["from"] == i] for i in self.nodes}
        self.order = topo_order(graph.get("nodes", []), graph.get("edges", [])) or list(self.nodes)
        self.ctx: dict = {
            "inputs": inputs,
            "vars": {v["name"]: v.get("value") for v in graph.get("variables") or []},
            "run": {"id": "essai", "started_at": "2026-01-01T00:00:00+00:00", "environment": "dev"},
            "steps": {},
        }
        self.state: dict[str, str] = {}
        self.decided: dict[str, bool] = {}
        self.chosen: dict[str, str] = {}  # multi-way choices: the case taken
        self.steps: list[dict] = []
        self.problems: list[dict] = []
        self.level = "error" if strict else "warning"

    # ---- bookkeeping
    def problem(self, nid, code, message):
        self.problems.append(
            {
                "node": nid,
                "level": self.level,
                "code": code,
                "message": f"{message} (essai « {self.label} »)",
                "scenario": self.label,
                "mechanical": self.mechanical,
            }
        )

    def record(self, nid, status, detail="", output=None):
        self.steps.append(
            {
                "id": nid,
                "name": self.nodes[nid].get("name") or nid,
                "type": self.nodes[nid]["type"],
                "state": status,
                "detail": detail,
                **({"output": output} if output is not None else {}),
            }
        )

    def lookup_failure(self, exc: ExpressionError) -> str:
        skipped = [s for s in re.findall(r"steps\.(\w+)", str(exc)) if self.state.get(s) == "skipped"]
        gone = (
            f" : l'étape « {self.nodes[skipped[0]].get('name') or skipped[0]} » n'a pas été exécutée sur ce chemin"
            if skipped
            else ""
        )
        return f"{exc}{gone}"

    def name(self, nid) -> str:
        return self.nodes[nid].get("name") or nid

    def done(self, nid, output, status, detail="", shown=None):
        """The step went well: its output is readable by the next ones."""
        self.ctx["steps"][nid] = {"output": output}
        self.state[nid] = "ok"
        self.record(nid, status, detail, shown)

    def failed(self, nid, status, detail, output=None):
        self.state[nid] = "failed"
        self.record(nid, status, detail, output)

    def need_error_exit(self, nid, message):
        """A failed step with no error exit ends the whole run."""
        if not any(e.get("label") == "error" for e in self.outgoing[nid]):
            raise _Fatal(message)

    def unreadable(self, nid, what, exc, ending):
        """An expression of the step cannot be read: a problem, the step fails, the run ends (`ending`)."""
        self.problem(nid, "dryrun_expression", f"{what} : {self.lookup_failure(exc)}")
        self.failed(nid, "failed", str(exc))
        raise _Fatal(f"{ending} : {exc}") from None

    # ---- walking
    def run(self, deadline: float, max_steps: int) -> dict:
        executed = 0
        try:
            for nid in self.order:
                if time.monotonic() > deadline or executed >= max_steps:
                    return {"status": "stopped", "message": "Essai interrompu (durée ou nombre d'étapes maximal)."}
                if self.incoming[nid] and not self.reached(nid):
                    self.state[nid] = "skipped"
                    self.record(nid, "skipped", "Chemin non emprunté dans cet essai.")
                    continue
                executed += 1
                self.step(nid, self.nodes[nid])
        except _Fatal as stop:
            text = str(stop)
            return {
                "status": "infra" if text.startswith("__infra__") else "failed",
                "message": text.removeprefix("__infra__:"),
            }
        return {"status": "succeeded", "message": ""}

    def reached(self, nid) -> bool:
        """At least one incoming edge is taken: from a step that went well by its own exit, or from a failed one by `error`."""
        for e in self.incoming[nid]:
            src, label = self.state.get(e["from"]), e.get("label") or "next"
            kind = self.nodes[e["from"]]["type"]
            if src == "ok":
                if kind == "condition":
                    taken = label == str(self.decided[e["from"]]).lower()
                elif kind == "switch":
                    taken = label == self.chosen[e["from"]]
                else:
                    taken = label == "next"
            else:
                taken = src == "failed" and label == "error"
            if taken:
                return True
        return False

    def step(self, nid, node):
        kind = node["type"]
        if nid in self.fails and kind in ("action", "subworkflow", "ai", "foreach"):  # (SSH handles `fails` itself)
            self.failed(
                nid, "simulated", "Échec simulé : on suit la sortie d'erreur."
            )  # the scenario is about the error exit
            self.need_error_exit(nid, "étape en échec")
        elif kind in _FIXED_STEPS:
            output, message = _FIXED_STEPS[kind]
            self.done(nid, output(node) if callable(output) else output, "simulated", message)
        else:
            getattr(self, "step_" + _STEP_METHODS.get(kind, "other"))(nid, node)

    # ---- one method per kind that does more than answer with a fixed output
    def step_script(self, nid, node):
        try:
            payload = script_payload(node, self.ctx)
        except ExpressionError as exc:
            self.problem(
                nid,
                "dryrun_expression",
                f"Une liaison du script « {self.name(nid)} » ne peut pas être lue : {self.lookup_failure(exc)}",
            )
            raise _Fatal(f"liaison illisible : {exc}") from None
        try:
            res = _run_script(self.runner, self.helpers, node, payload)
        except Exception as exc:  # noqa: BLE001  (the runner itself is unavailable: not the script's fault)
            log.exception("essai à blanc : le lanceur d'actions est indisponible")
            raise _Fatal(f"__infra__:{type(exc).__name__} : {exc}"[:300]) from None
        if (res.error or {}).get("code") in INFRA_CODES:
            raise _Fatal(f"__infra__:{res.error.get('message')}")
        if not res.ok:
            why, where, at = _crash(res)
            if not _deliberate(node.get("script", ""), at):
                self.problem(nid, "dryrun_script_error", f"Le script « {self.name(nid)} » plante{where} : {why}")
            self.failed(nid, "failed", f"{why}{where}")
            self.need_error_exit(nid, f"script en échec : {why}")
            return
        wrong = _script_output_problem(node, res.result)
        if wrong:
            self.problem(nid, "dryrun_output", f"Script « {self.name(nid)} » : {wrong}")
            self.failed(nid, "failed", wrong)
            self.need_error_exit(nid, wrong)
            return
        self.done(
            nid, res.result, "ok", "Exécuté dans le bac à sable.", {k: _short(v) for k, v in (res.result or {}).items()}
        )

    def step_condition(self, nid, node):
        try:
            self.decided[nid] = bool(evaluate(_placeholders(node.get("expr")), self.ctx))
        except ExpressionError as exc:
            self.unreadable(
                nid, f"La décision « {self.name(nid)} » ne peut pas être évaluée", exc, "décision illisible"
            )
        self.done(nid, {"result": self.decided[nid]}, "ok", "Oui" if self.decided[nid] else "Non")

    def step_switch(self, nid, node):
        forced_case = (self.pick or {}).get(nid)
        try:
            self.chosen[nid] = forced_case or next(
                (c["id"] for c in node.get("cases") or [] if evaluate(_placeholders(c.get("expr")), self.ctx)),
                "default",
            )
        except ExpressionError as exc:
            self.unreadable(nid, f"Le choix « {self.name(nid)} » ne peut pas être évalué", exc, "choix illisible")
        self.done(
            nid,
            {"result": self.chosen[nid]},
            "ok",
            f"Cas « {self.chosen[nid]} »" + (" (imposé par l'essai)" if forced_case else ""),
        )

    def step_throw(self, nid, node):
        self.failed(nid, "failed", str(node.get("message") or "Exception"))
        raise _Fatal(f"exception « {node.get('message') or nid} »")

    def step_approval(self, nid, node):
        if self.approve:
            self.done(
                nid,
                {"approved": True, "decided_by": "essai", "comment": ""},
                "simulated",
                "Approbation simulée : accordée.",
            )
            return
        self.failed(nid, "simulated", "Approbation simulée : refusée.")
        self.need_error_exit(nid, "approbation refusée")

    def step_foreach(self, nid, node):
        try:
            listed = evaluate(_placeholders(node.get("items")), self.ctx)
            resolve(node.get("inputs") or {}, {**self.ctx, "inputs": self.ctx["inputs"]})
        except ExpressionError as exc:
            self.unreadable(nid, f"La boucle « {self.name(nid)} » ne peut pas lire sa liste", exc, "boucle illisible")
        if not isinstance(listed, list):
            self.problem(
                nid,
                "dryrun_expression",
                f"La boucle « {self.name(nid)} » doit parcourir une liste, elle reçoit {type(listed).__name__}.",
            )
            self.failed(nid, "failed", "Ce n'est pas une liste.")
            raise _Fatal("boucle sur autre chose qu'une liste")
        child = self.helpers["loops"].get(nid, {})
        self.done(
            nid,
            {"count": len(listed), "results": [dict(child) for _ in listed[:3]]},
            "simulated",
            f"Boucle simulée : {len(listed)} élément(s), {min(len(listed), 3)} résultat(s) d'exemple.",
        )

    def step_call(self, nid, node):
        """Actions, sub-workflows and AI steps: simulated from their contract, after reading their values."""
        try:
            resolve(node.get("inputs") or {}, self.ctx)
            if node["type"] == "ai" and node.get("prompt"):
                resolve(node["prompt"], self.ctx)
        except ExpressionError as exc:
            self.problem(
                nid,
                "dryrun_expression",
                f"Une valeur de l'étape « {self.name(nid)} » ne peut pas être lue : {self.lookup_failure(exc)}",
            )
            self.failed(nid, "failed", str(exc))
            self.need_error_exit(nid, f"valeur illisible : {exc}")
            return
        output = {**self.helpers["contracts"].get(nid, {}), **self.forced.get(nid, {})}
        self.done(nid, output, "simulated", "Simulée : sortie construite d'après son contrat.")

    def step_transform(self, nid, node):
        from workflows import transforms

        try:
            produced = transforms.apply(node, self.ctx)
            note = "Transformation exécutée sur les données d'essai."
        except transforms.TransformError:
            produced = {
                k: ([] if v.get("type") == "array" else 0 if v.get("type") == "integer" else "")
                for k, v in transforms.output_fields(node).items()
            }
            note = "Transformation simulée (données d'essai inexploitables : sortie vide)."
        self.done(nid, produced, "simulated", note)

    def step_ssh(self, nid, node):
        out = ssh_node.dryrun_output(node, self.ctx, self.helpers["ssh"], force_failure=nid in self.fails)
        self.ctx["steps"][nid] = {
            "output": out
        }  # partial results stay readable by the `error` branch, as for the real step
        if out["status"] == "failed":
            self.failed(nid, "simulated", "Commandes SSH simulées : échec simulé, on suit la sortie d'erreur.", out)
            self.need_error_exit(nid, "étape en échec")
        else:
            self.state[nid] = "ok"
            self.record(nid, "simulated", "Commandes SSH simulées : aucune connexion n'est ouverte.", out)

    def step_other(self, nid, node):  # wait, join
        kind = node["type"]
        output = {"resumed": True, "payload": {}} if node.get("resume") == "webhook" else {}
        self.done(
            nid,
            output,
            "simulated" if kind == "wait" else "ok",
            "Attente simulée (non attendue)." if kind == "wait" else "",
        )


_STEP_METHODS = {
    "script": "script",
    "condition": "condition",
    "switch": "switch",
    "throw": "throw",
    "approval": "approval",
    "foreach": "foreach",
    "action": "call",
    "subworkflow": "call",
    "ai": "call",
    "transform": "transform",
    "ssh": "ssh",
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
    simulated = {n["id"] for n in graph.get("nodes", []) if n["type"] in ("action", "subworkflow", "ai", "cache")}
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


_PlanEntry = tuple[dict, bool, "int | str | set | None", "dict | tuple | None"]


def _scenario_plan(graph: dict, samples: list[dict]) -> list[_PlanEntry]:
    """Every scenario to run, in order: the samples, approvals refused, each failing exit, each awaited value, each case."""
    plan: list[tuple[dict, bool, int | str | set | None, dict | tuple | None]] = [
        (s, True, None, None) for s in samples[:3]
    ]
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
        if n["type"] in ("action", "subworkflow", "foreach", "ai", "ssh")
        and any(e["from"] == n["id"] and e.get("label") == "error" for e in graph.get("edges", []))
    ][:MAX_FAILURE_SCENARIOS]
    awaited = _awaited_values(graph)
    reach_all: dict[
        str, dict
    ] = {}  # every value a decision waits for, applied together: the branches that hold the steps worth failing
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
            if n["type"] in ("action", "subworkflow", "foreach", "ai", "ssh")
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
    return plan


def dry_run(
    graph: dict,
    workspace,
    runner,
    samples: list[dict],
    progress=None,
    cache: dict | None = None,
    max_steps: int = MAX_STEPS,
    budget: float = TOTAL_BUDGET,
) -> dict:
    """Run the scenarios: each sample (label, inputs, expected) with approvals granted, plus one with approvals refused
    when the workflow has any. Returns {scenarios, problems, coverage, ran}."""
    deadline = time.monotonic() + budget
    plan = _scenario_plan(graph, samples)[:MAX_SCENARIOS]
    helpers = prepare(graph, workspace, cache)  # database reads happen here, once; scenarios only use the runner

    def one(i):
        sample, approve, fail, force = plan[i]
        picked = force.get("__pick__") if isinstance(force, dict) else None
        force = None if picked else force
        if force is None and not picked and isinstance(sample.get("mocks"), dict):
            force = sample["mocks"]  # sorties imposées à des étapes SIMULÉES (action, IA…) : {étape: {champ: valeur}}
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
            max_steps=max_steps,
        )

    # The scenarios are independent: they run side by side (starting a container is slow, waiting for each in turn is not).
    results: dict[int, dict] = {}
    problems: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(plan), MAX_PARALLEL) or 1) as pool:
        futures = {pool.submit(one, i): i for i in range(len(plan))}
        done = 0
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            done += 1
            if progress:
                progress(done - 1, len(plan), plan[i][0]["label"], done)
    scenarios = [results[i] for i in range(len(plan))]
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
