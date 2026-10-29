"""The dry run: scripts and decisions really run in the sandbox on sample inputs; the rest is simulated from contracts."""

import time

from ai import dryrun
from runs.runners import SubprocessRunner
from tests.test_ai import ai_ws  # noqa: F401  (fixture)


def script(nid, code, bindings=None, outputs=(("x", "integer"),), **kw):
    return {
        "id": nid,
        "type": "script",
        "name": nid.title(),
        "language": "python",
        "script": code,
        "bindings": bindings or {},
        "outputs": [{"name": n, "type": t} for n, t in outputs],
        "timeout_seconds": 10,
        **kw,
    }


def graph(nodes, edges, **kw):
    return {
        "inputs": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "liste": {"type": "array"}},
            "required": [],
        },
        "nodes": nodes,
        "edges": edges,
        "outputs": {},
        **kw,
    }


def scenario(ws, g, inputs, **kw):
    return dryrun.run_scenario(
        g,
        ws,
        SubprocessRunner(),
        label=kw.pop("label", "essai"),
        inputs=inputs,
        approve=kw.pop("approve", True),
        strict=kw.pop("strict", True),
        deadline=time.monotonic() + 60,
        expected=kw.pop("expected", None),
    )


def codes(sc):
    return [p["code"] for p in sc["problems"]]


def test_sample_values_follow_the_schema():
    schema = {
        "type": "object",
        "properties": {
            "nom": {"type": "string"},
            "nb": {"type": "integer", "minimum": 5},
            "ok": {"type": "boolean"},
            "l": {"type": "array", "items": {"type": "object", "properties": {"q": {"type": "integer"}}}},
            "e": {"enum": ["a", "b"]},
            "d": {"type": "string", "default": "x"},
        },
    }
    nominal, edge = dryrun.sample_inputs(schema, "nominal"), dryrun.sample_inputs(schema, "limite")
    assert nominal == {"nom": "exemple", "nb": 5, "ok": True, "l": [{"q": 3}, {"q": 3}], "e": "a", "d": "x"}
    assert edge["l"] == [] and edge["ok"] is False and edge["nom"] == "" and dryrun.valid_inputs(schema, nominal)


def test_scripts_and_decisions_run_for_real_and_the_branch_follows_the_values(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            script("calc", "x = n * 2", {"n": "{{ inputs.n }}"}),
            {"id": "d", "type": "condition", "name": "D", "expr": "steps.calc.output.x > 10"},
            {"id": "gros", "type": "wait", "name": "Gros", "seconds": 1},
            {"id": "petit", "type": "wait", "name": "Petit", "seconds": 1},
        ],
        [
            {"from": "calc", "to": "d"},
            {"from": "d", "to": "gros", "label": "true"},
            {"from": "d", "to": "petit", "label": "false"},
        ],
    )
    big = scenario(ws, g, {"n": 8})
    assert big["outcome"]["status"] == "succeeded" and codes(big) == []
    states = {s["id"]: s["state"] for s in big["steps"]}
    assert states == {"calc": "ok", "d": "ok", "gros": "simulated", "petit": "skipped"}
    assert next(s for s in big["steps"] if s["id"] == "calc")["output"] == {"x": "16"}
    small = scenario(ws, g, {"n": 1})
    assert {s["id"]: s["state"] for s in small["steps"]}["petit"] == "simulated"


def test_a_script_that_crashes_is_reported_with_the_line_and_the_error(ai_ws):
    ws, _ = ai_ws
    g = graph([script("calc", "a = 1\nb = a / n\nx = 1", {"n": "{{ inputs.n }}"})], [])
    sc = scenario(ws, g, {"n": 0})
    assert codes(sc) == ["dryrun_script_error"] and sc["outcome"]["status"] == "failed"
    msg = sc["problems"][0]["message"]
    assert (
        "ligne 2" in msg
        and "ZeroDivisionError" in msg
        and "essai « essai »" in msg
        and sc["problems"][0]["level"] == "error"
    )
    edge = scenario(ws, g, {"n": 0}, strict=False)
    assert edge["problems"][0]["level"] == "warning"  # an edge case may legitimately fail: a warning, not an error


def test_declared_outputs_must_exist_and_have_the_declared_type(ai_ws):
    ws, _ = ai_ws
    never = scenario(ws, graph([script("a", "y = 1")], []), {})  # x never set
    assert codes(never) == ["dryrun_script_error"] and "x" in never["problems"][0]["message"]
    assert codes(scenario(ws, graph([script("a", "x = 'texte'")], []), {})) == ["dryrun_output"]  # wrong type


def test_an_error_exit_catches_a_failing_script_and_the_run_goes_on(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            script("a", "x = 1/0"),
            {"id": "plan_b", "type": "wait", "name": "B", "seconds": 1},
            {"id": "suite", "type": "wait", "name": "S", "seconds": 1},
        ],
        [{"from": "a", "to": "plan_b", "label": "error"}, {"from": "a", "to": "suite"}],
    )
    sc = scenario(ws, g, {})
    states = {s["id"]: s["state"] for s in sc["steps"]}
    assert (
        states == {"a": "failed", "plan_b": "simulated", "suite": "skipped"} and sc["outcome"]["status"] == "succeeded"
    )


def test_an_exception_ends_the_run_and_is_the_outcome_not_a_problem(ai_ws):
    ws, _ = ai_ws
    g = graph([{"id": "t", "type": "throw", "name": "T", "message": "Montant incohérent"}], [])
    sc = scenario(ws, g, {})
    assert sc["outcome"] == {"status": "failed", "message": "exception « Montant incohérent »"} and sc["problems"] == []


def test_a_decision_reading_a_missing_field_or_a_skipped_step_is_found(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {"id": "d", "type": "condition", "name": "D", "expr": "inputs.absent > 1"},
            {"id": "w", "type": "wait", "name": "W", "seconds": 1},
        ],
        [{"from": "d", "to": "w", "label": "true"}],
    )
    assert codes(scenario(ws, g, {})) == ["dryrun_expression"]
    # a step read on a path where it never ran (the real hazard behind the « étape ignorée » warning)
    g2 = graph(
        [
            {"id": "d", "type": "condition", "name": "D", "expr": "inputs.n > 1"},
            script("a", "x = 1"),
            {"id": "j", "type": "join", "name": "J"},
            script("b", "x = y", {"y": "{{ steps.a.output.x }}"}),
        ],
        [
            {"from": "d", "to": "a", "label": "true"},
            {"from": "a", "to": "j"},
            {"from": "d", "to": "j", "label": "false"},
            {"from": "j", "to": "b"},
        ],
    )
    sc = scenario(ws, g2, {"n": 0})
    assert codes(sc) == ["dryrun_expression"] and "n'a pas été exécutée sur ce chemin" in sc["problems"][0]["message"]
    assert codes(scenario(ws, g2, {"n": 5})) == []


def test_approvals_are_simulated_both_ways_and_the_refusal_follows_its_error_exit(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {"id": "ap", "type": "approval", "name": "Ap", "title": "t"},
            {"id": "no", "type": "throw", "name": "No", "message": "Refusé"},
            {"id": "ok", "type": "wait", "name": "Ok", "seconds": 1},
        ],
        [{"from": "ap", "to": "no", "label": "error"}, {"from": "ap", "to": "ok"}],
    )
    yes, no = scenario(ws, g, {}), scenario(ws, g, {}, approve=False)
    assert yes["outcome"]["status"] == "succeeded" and no["outcome"]["message"] == "exception « Refusé »"
    assert "ok" not in {s["id"] for s in no["steps"] if s["state"] != "skipped"}  # the success path was not taken


def test_a_loop_gets_example_results_built_from_the_called_workflow_contract(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {
                "id": "b",
                "type": "foreach",
                "name": "B",
                "workflow": "inconnu",
                "items": "inputs.liste",
                "item_input": "i",
                "max_iterations": 5,
            },
            script("s", "x = len(r)", {"r": "{{ steps.b.output.results }}"}),
        ],
        [{"from": "b", "to": "s"}],
    )
    sc = scenario(ws, g, {"liste": ["a", "b", "c", "d"]})
    assert codes(sc) == [] and next(s for s in sc["steps"] if s["id"] == "s")["output"] == {
        "x": "3"
    }  # 3 example results at most
    assert codes(scenario(ws, g, {"liste": "pas une liste"})) == ["dryrun_expression"]


def test_the_results_the_request_states_are_compared_with_what_the_script_produced(ai_ws):
    ws, _ = ai_ws
    g = graph([script("calc", "x = n + 1", {"n": "{{ inputs.n }}"})], [])
    ok = scenario(ws, g, {"n": 1}, expected=[{"step": "calc", "field": "x", "value": 2}])
    assert ok["problems"] == []
    bad = scenario(ws, g, {"n": 1}, expected=[{"step": "calc", "field": "x", "value": 3}])
    assert bad["problems"][0]["code"] == "dryrun_unexpected" and "attendu 3, obtenu 2" in bad["problems"][0]["message"]
    assert bad["problems"][0]["level"] == "warning"  # either side may be wrong: a person decides


def test_a_runner_that_is_not_available_gives_no_verdict(ai_ws):
    ws, _ = ai_ws

    class Broken:
        def run(self, *a, **k):
            raise OSError("pas de docker")

    g = graph([script("a", "x = 1")], [])
    out = dryrun.dry_run(g, ws, Broken(), [{"label": "nominal", "inputs": {}}])
    assert out["ran"] is False and "pas de docker" in out["reason"] and out["problems"] == []


def test_the_whole_dry_run_reports_scenarios_coverage_and_never_executed_steps(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {"id": "d", "type": "condition", "name": "Décision", "expr": "inputs.n > 1"},
            {"id": "a", "type": "wait", "name": "Oui", "seconds": 1},
            {"id": "b", "type": "wait", "name": "Non", "seconds": 1},
        ],
        [{"from": "d", "to": "a", "label": "true"}, {"from": "d", "to": "b", "label": "false"}],
    )
    samples = [{"label": "nominal", "inputs": {"n": 5}}, {"label": "limite", "inputs": {"n": 0}}]
    out = dryrun.dry_run(g, ws, SubprocessRunner(), samples)
    assert out["ran"] and len(out["scenarios"]) == 2 and out["coverage"] == {"executed": 3, "total": 3, "never": []}
    one = dryrun.dry_run(g, ws, SubprocessRunner(), samples[:1])
    assert one["coverage"]["never"] == ["Non"]  # a branch no sample reached is said
    s = dryrun.summary(out)
    assert s["ran"] and s["scenarios"][0]["label"] == "nominal" and "inputs" not in s["scenarios"][0]


def test_identical_script_runs_are_done_once_and_scenarios_run_side_by_side(ai_ws):
    ws, _ = ai_ws
    calls = []

    class Counting(SubprocessRunner):
        def run(self, version, inputs, timeout, *a, **k):
            calls.append(inputs["script"])
            return super().run(version, inputs, timeout, *a, **k)

    g = graph(
        [
            script("calc", "x = n + 1", {"n": "{{ inputs.n }}"}),
            {"id": "ap", "type": "approval", "name": "Ap", "title": "t"},
            {"id": "no", "type": "throw", "name": "No", "message": "Refusé"},
        ],
        [{"from": "calc", "to": "ap"}, {"from": "ap", "to": "no", "label": "error"}],
    )
    out = dryrun.dry_run(g, ws, Counting(), [{"label": "a", "inputs": {"n": 1}}, {"label": "b", "inputs": {"n": 2}}])
    assert [s["label"] for s in out["scenarios"]] == [
        "a",
        "b",
        "a, approbation refusée",
        "b, approbation refusée",
    ]  # order kept whatever finishes first
    assert len(calls) == 2  # the refused-approval twins repeat the same scripts: not run again


def test_every_error_exit_of_a_simulated_step_gets_a_scenario_where_that_step_fails(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {"id": "act", "type": "action", "name": "Appel", "action": "inconnue", "inputs": {}},
            {"id": "plan_b", "type": "wait", "name": "Plan B", "seconds": 1},
            {"id": "ko", "type": "throw", "name": "KO", "message": "Création échouée"},
            {"id": "suite", "type": "wait", "name": "Suite", "seconds": 1},
        ],
        [
            {"from": "act", "to": "plan_b", "label": "error"},
            {"from": "plan_b", "to": "ko"},
            {"from": "act", "to": "suite"},
        ],
    )
    out = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    labels = [s["label"] for s in out["scenarios"]]
    assert labels == ["nominal", "« Appel » en échec (simulé)"]
    nominal, failed = out["scenarios"]
    assert {s["id"]: s["state"] for s in nominal["steps"]}["suite"] == "simulated"
    assert failed["outcome"]["message"] == "exception « Création échouée »"  # the whole error chain was walked
    assert out["coverage"]["never"] == []  # nothing left unexercised


def test_a_refused_approval_with_no_error_exit_is_reported_once_it_is_really_reached(ai_ws):
    ws, _ = ai_ws
    g = graph(
        [
            {"id": "ap", "type": "approval", "name": "Accord", "title": "t"},
            {"id": "suite", "type": "wait", "name": "S", "seconds": 1},
        ],
        [{"from": "ap", "to": "suite"}],
    )
    out = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    hit = [p for p in out["problems"] if p["code"] == "dryrun_refusal"]
    assert (
        len(hit) == 1
        and hit[0]["node"] == "ap"
        and "sans message ni notification" in hit[0]["message"]
        and hit[0]["level"] == "warning"
    )
    handled = graph(
        [
            {"id": "ap", "type": "approval", "name": "Accord", "title": "t"},
            {"id": "no", "type": "throw", "name": "N", "message": "Refusé"},
        ],
        [{"from": "ap", "to": "no", "label": "error"}],
    )
    assert not [
        p
        for p in dryrun.dry_run(handled, ws, SubprocessRunner(), [{"label": "n", "inputs": {}}])["problems"]
        if p["code"] == "dryrun_refusal"
    ]


def test_a_decision_waiting_for_a_value_from_a_simulated_step_gets_a_scenario_where_it_comes(ai_ws, world):
    ws, _ = ai_ws
    from tests.wf_helpers import STR, publish
    from tests.wf_helpers import node as wnode
    from tests.wf_helpers import schema as wschema

    publish(
        ws,
        "diag",
        [wnode("e", action="text-stats", inputs={"text": "ok"})],
        [],
        inputs=wschema([], ip=STR),
        outputs={"etat": "{{ steps.e.output.longest_word }}"},
        user=world["designer"],
    )
    g = graph(
        [
            {"id": "d", "type": "subworkflow", "name": "Diagnostic", "workflow": "diag", "inputs": {}},
            {"id": "c", "type": "condition", "name": "Plantée ?", "expr": "steps.d.output.etat == 'plantée'"},
            {"id": "redem", "type": "wait", "name": "Redémarrage", "seconds": 1},
            {"id": "ok", "type": "wait", "name": "Rien à faire", "seconds": 1},
        ],
        [
            {"from": "d", "to": "c"},
            {"from": "c", "to": "redem", "label": "true"},
            {"from": "c", "to": "ok", "label": "false"},
        ],
    )
    out = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    labels = [s["label"] for s in out["scenarios"]]
    assert "« Diagnostic » renvoie etat = « plantée » (simulé)" in labels
    forced = next(s for s in out["scenarios"] if "renvoie" in s["label"])
    assert {x["id"]: x["state"] for x in forced["steps"]}["redem"] == "simulated"  # the branch is walked
    assert out["coverage"]["never"] == []


def test_the_awaited_values_are_read_from_plain_equalities_only():
    g = {
        "nodes": [
            {"id": "a", "type": "action"},
            {"id": "b", "type": "ai"},
            {"id": "s", "type": "script"},
            {"id": "c1", "type": "condition", "expr": "steps.a.output.n == 3"},
            {"id": "c2", "type": "condition", "expr": "steps.b.output.ok == true"},
            {"id": "c3", "type": "condition", "expr": "steps.s.output.x == 'a'"},
            {"id": "c4", "type": "condition", "expr": "steps.a.output.n > 3"},
        ],
        "edges": [],
    }
    got = dryrun._awaited_values(g)
    assert [(a, f, v) for a, f, v, _ in got] == [
        ("a", "n", 3),
        ("b", "ok", True),
    ]  # a script's output is real, not simulated; > is not an equality


def test_the_assistant_may_set_automatic_retries_on_actions_scripts_and_ai_steps():
    from ai import service as svc

    kept = svc._clean_node(
        {"id": "a", "type": "action", "action": "x", "retry": {"max_attempts": 9, "backoff_seconds": 10}}
    )
    assert kept["retry"] == {"max_attempts": 5, "backoff_seconds": 10, "jitter": 1}
    assert "retry" not in svc._clean_node({"id": "a", "type": "action", "action": "x", "retry": {"max_attempts": 1}})
    assert "retry" not in svc._clean_node(
        {"id": "w", "type": "subworkflow", "workflow": "x", "retry": {"max_attempts": 3}}
    )  # the engine does not retry these


def test_a_script_that_raises_on_purpose_is_not_reported_as_a_crash(ai_ws):
    ws, _ = ai_ws
    g = graph([script("verif", "if n < 1:\n    raise Exception('Nom invalide')\nx = 1", {"n": "{{ inputs.n }}"})], [])
    deliberate = scenario(ws, g, {"n": 0}, strict=False)
    assert (
        codes(deliberate) == [] and deliberate["outcome"]["status"] == "failed"
    )  # it stops, as written, and that is no bug
    bug = graph([script("verif", "x = 1 / n", {"n": "{{ inputs.n }}"})], [])
    assert codes(scenario(ws, bug, {"n": 0}, strict=False)) == ["dryrun_script_error"]  # an accident is still found


def test_a_chain_of_retries_is_walked_when_every_link_fails_in_turn(ai_ws, world):
    ws, _ = ai_ws
    from tests.wf_helpers import STR, publish
    from tests.wf_helpers import node as wnode
    from tests.wf_helpers import schema as wschema

    publish(
        ws,
        "redem",
        [wnode("e", action="text-stats", inputs={"text": "ok"})],
        [],
        inputs=wschema([], ip=STR),
        outputs={"statut": "{{ steps.e.output.longest_word }}"},
        user=world["designer"],
    )
    call = lambda i, n: {"id": i, "type": "subworkflow", "name": n, "workflow": "redem", "inputs": {}}  # noqa: E731
    g = graph(
        [
            call("r1", "Redémarrage"),
            {"id": "p1", "type": "wait", "name": "Attente 1", "seconds": 1},
            call("r2", "Redémarrage (2)"),
            {"id": "p2", "type": "wait", "name": "Attente 2", "seconds": 1},
            call("r3", "Redémarrage (3)"),
            {"id": "inc", "type": "throw", "name": "Incident", "message": "Redémarrage impossible"},
        ],
        [
            {"from": "r1", "to": "p1", "label": "error"},
            {"from": "p1", "to": "r2"},
            {"from": "r2", "to": "p2", "label": "error"},
            {"from": "p2", "to": "r3"},
            {"from": "r3", "to": "inc", "label": "error"},
        ],
    )
    out = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "nominal", "inputs": {}}])
    assert out["coverage"]["never"] == []  # the whole recovery chain, down to the last exception, was exercised
    chain = next(s for s in out["scenarios"] if s["label"].startswith("chaque étape"))
    assert chain["outcome"]["message"] == "exception « Redémarrage impossible »"


def test_a_multi_way_choice_follows_its_case_and_every_case_gets_a_scenario(ai_ws):
    ws, _ = ai_ws
    wait = lambda i: {"id": i, "type": "wait", "name": i.title(), "seconds": 1}  # noqa: E731
    g = graph(
        [
            {
                "id": "s",
                "type": "switch",
                "name": "Choix",
                "cases": [{"id": "gros", "expr": "inputs.n > 10"}, {"id": "moyen", "expr": "inputs.n > 5"}],
            },
            wait("a"),
            wait("b"),
            wait("c"),
        ],
        [
            {"from": "s", "to": "a", "label": "gros"},
            {"from": "s", "to": "b", "label": "moyen"},
            {"from": "s", "to": "c", "label": "default"},
        ],
    )
    sc = scenario(ws, g, {"n": 7})
    assert {s["id"]: s["state"] for s in sc["steps"]} == {"s": "ok", "a": "skipped", "b": "simulated", "c": "skipped"}
    result = dryrun.dry_run(g, ws, SubprocessRunner(), [{"label": "typique", "inputs": {"n": 1}, "expected": []}])
    labels = [s["label"] for s in result["scenarios"]]
    assert sum("prend le cas" in label for label in labels) == 3
    assert result["ran"]
