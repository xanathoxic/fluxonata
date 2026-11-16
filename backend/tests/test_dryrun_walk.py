"""Essai à blanc : parcours des étapes simulées, sans base ni bac à sable (assistants fournis à la main).

Écrits avant le découpage de `run_scenario` : ils figent la sortie simulée de chaque type d'étape, l'ordre des étapes, les
chemins non pris, les échecs imposés et les problèmes signalés."""

import time

from ai import dryrun

HELPERS = {"loops": {"each": {"name": "x"}}, "contracts": {}, "ssh": {}, "cache": {}, "versions": {}}


def n(nid, type_, **kw):
    return {"id": nid, "type": type_, "name": nid.upper(), **kw}


def e(a, b, label=None):
    return {"from": a, "to": b, **({"label": label} if label else {})}


def walk(nodes, edges, inputs=None, **kw):
    g = {"inputs": {}, "nodes": nodes, "edges": edges, "outputs": {}, "variables": kw.pop("variables", [])}
    return dryrun.run_scenario(
        g,
        None,
        None,
        label=kw.pop("label", "essai"),
        inputs=inputs or {},
        approve=kw.pop("approve", True),
        strict=kw.pop("strict", True),
        deadline=kw.pop("deadline", time.monotonic() + 60),
        helpers=HELPERS,
        **kw,
    )


def by_id(sc):
    return {s["id"]: s for s in sc["steps"]}


def test_every_simulated_kind_has_its_fixed_output_and_message():
    kinds = ["smtp", "sftp", "ldap", "while", "agent", "sql", "http", "wait", "join"]
    nodes = [n(k, k) for k in kinds]
    edges = [e(a, b) for a, b in zip(kinds, kinds[1:], strict=False)]
    sc = walk(nodes, edges)
    assert sc["outcome"] == {"status": "succeeded", "message": ""} and sc["problems"] == []
    steps = by_id(sc)
    assert [s["id"] for s in sc["steps"]] == kinds
    assert steps["smtp"]["detail"] == "E-mail simulé : rien n'est envoyé." and steps["smtp"]["state"] == "simulated"
    assert steps["sftp"]["detail"].startswith("Fichier SFTP simulé")
    assert steps["ldap"]["detail"].startswith("Recherche d'annuaire simulée")
    assert steps["while"]["detail"].startswith("Boucle « tant que » simulée")
    assert steps["agent"]["detail"].startswith("Agent simulé")
    assert steps["sql"]["detail"] == "Requête SQL simulée : rien n'est envoyé."
    assert steps["http"]["detail"] == "Requête HTTP simulée : rien n'est envoyé."
    assert steps["wait"] == {
        "id": "wait",
        "name": "WAIT",
        "type": "wait",
        "state": "simulated",
        "detail": "Attente simulée (non attendue).",
    }
    assert steps["join"]["state"] == "ok" and steps["join"]["detail"] == ""


def test_outputs_of_simulated_steps_are_readable_by_later_expressions():
    nodes = [
        n("q", "sql"),
        n("c", "condition", expr="{{ steps.q.output.count == 0 }}"),
        n("yes", "http"),
        n("no", "smtp"),
    ]
    sc = walk(nodes, [e("q", "c"), e("c", "yes", "true"), e("c", "no", "false")])
    steps = by_id(sc)
    assert steps["c"]["detail"] == "Oui" and steps["yes"]["state"] == "simulated"
    assert steps["no"] == {
        "id": "no",
        "name": "NO",
        "type": "smtp",
        "state": "skipped",
        "detail": "Chemin non emprunté dans cet essai.",
    }


def test_a_failed_step_follows_only_its_error_exit():
    nodes = [n("a", "action"), n("ok", "http"), n("ko", "smtp")]
    sc = walk(nodes, [e("a", "ok"), e("a", "ko", "error")], fail="a")
    steps = by_id(sc)
    assert steps["a"]["detail"] == "Échec simulé : on suit la sortie d'erreur." and steps["ko"]["state"] == "simulated"
    assert steps["ok"]["state"] == "skipped" and sc["outcome"]["status"] == "succeeded"


def test_a_forced_failure_without_error_exit_ends_the_run():
    sc = walk([n("a", "action"), n("b", "http")], [e("a", "b")], fail={"a"})
    assert sc["outcome"] == {"status": "failed", "message": "étape en échec"} and [s["id"] for s in sc["steps"]] == [
        "a"
    ]


def test_forced_outputs_and_contracts_feed_simulated_actions():
    helpers = {**HELPERS, "contracts": {"a": {"id": 1, "name": "x"}}}
    g = {
        "inputs": {},
        "nodes": [n("a", "action"), n("c", "condition", expr="steps.a.output.id == 7")],
        "edges": [e("a", "c")],
        "outputs": {},
    }
    sc = dryrun.run_scenario(
        g, None, None, label="l", inputs={}, approve=True, strict=True, deadline=time.monotonic() + 9, helpers=helpers,
        force=("a", "id", 7),
    )  # fmt: skip
    assert by_id(sc)["c"]["detail"] == "Oui"


def test_a_throw_ends_the_run_with_its_message():
    sc = walk([n("t", "throw", message="boum")], [])
    assert sc["outcome"] == {"status": "failed", "message": "exception « boum »"}
    assert by_id(sc)["t"] == {"id": "t", "name": "T", "type": "throw", "state": "failed", "detail": "boum"}


def test_a_decision_reading_a_missing_field_is_a_problem_and_ends_the_run():
    sc = walk([n("c", "condition", expr="inputs.absent > 1")], [])
    assert sc["outcome"]["status"] == "failed" and sc["outcome"]["message"].startswith("décision illisible")
    assert [p["code"] for p in sc["problems"]] == ["dryrun_expression"] and sc["problems"][0]["level"] == "error"
    assert sc["problems"][0]["message"].endswith("(essai « essai »)") and sc["problems"][0]["scenario"] == "essai"


def test_problems_are_warnings_outside_the_nominal_scenario_and_carry_the_mechanical_flag():
    sc = walk([n("c", "condition", expr="inputs.absent > 1")], [], strict=False, mechanical=True)
    assert sc["problems"][0]["level"] == "warning" and sc["problems"][0]["mechanical"] is True


def test_a_skipped_step_is_named_when_a_later_expression_reads_it():
    nodes = [
        n("c", "condition", expr="true"),
        n("a", "http"),
        n("b", "http"),
        n("j", "join"),
        n("d", "condition", expr="steps.b.output.status == 200"),
    ]
    edges = [e("c", "a", "true"), e("c", "b", "false"), e("a", "j"), e("b", "j"), e("j", "d")]
    sc = walk(nodes, edges)
    assert (
        "n'a pas été exécutée sur ce chemin" in sc["problems"][0]["message"] and "« B »" in sc["problems"][0]["message"]
    )


def test_a_multi_way_choice_picks_the_first_matching_case_or_the_forced_one():
    cases = [{"id": "un", "expr": "inputs.n == 1"}, {"id": "deux", "expr": "inputs.n == 2"}]
    nodes = [n("s", "switch", cases=cases), n("u", "http"), n("d", "http"), n("z", "http")]
    edges = [e("s", "u", "un"), e("s", "d", "deux"), e("s", "z", "default")]
    steps = by_id(walk(nodes, edges, {"n": 2}))
    assert (
        steps["s"]["detail"] == "Cas « deux »"
        and steps["d"]["state"] == "simulated"
        and steps["u"]["state"] == "skipped"
    )
    assert by_id(walk(nodes, edges, {"n": 9}))["s"]["detail"] == "Cas « default »"
    forced = by_id(walk(nodes, edges, {"n": 2}, pick={"s": "un"}))
    assert forced["s"]["detail"] == "Cas « un » (imposé par l'essai)" and forced["u"]["state"] == "simulated"


def test_a_loop_reads_its_list_and_must_get_a_list():
    ok = walk([n("each", "foreach", items="inputs.l", inputs={})], [], {"l": [1, 2, 3, 4]})
    assert by_id(ok)["each"]["detail"] == "Boucle simulée : 4 élément(s), 3 résultat(s) d'exemple."
    bad = walk([n("each", "foreach", items="inputs.l", inputs={})], [], {"l": "texte"})
    assert (
        bad["outcome"]["message"] == "boucle sur autre chose qu'une liste"
        and bad["problems"][0]["code"] == "dryrun_expression"
    )
    unreadable = walk([n("each", "foreach", items="inputs.zz", inputs={})], [], {})
    assert unreadable["outcome"]["message"].startswith("boucle illisible")


def test_approval_granted_or_refused_and_the_refusal_without_error_exit_is_a_warning():
    nodes = [n("p", "approval"), n("after", "http")]
    assert by_id(walk(nodes, [e("p", "after")]))["p"]["detail"] == "Approbation simulée : accordée."
    sc = walk(nodes, [e("p", "after")], approve=False)
    assert sc["outcome"] == {"status": "failed", "message": "approbation refusée"}
    assert [p["code"] for p in sc["problems"]] == ["dryrun_refusal"] and sc["problems"][0]["level"] == "warning"
    rescued = walk(nodes + [n("m", "smtp")], [e("p", "after"), e("p", "m", "error")], approve=False)
    assert rescued["outcome"]["status"] == "succeeded" and rescued["problems"] == []


def test_unreadable_values_of_an_action_follow_the_error_exit_or_end_the_run():
    nodes = [n("a", "action", inputs={"x": "{{ inputs.absent }}"}), n("h", "smtp")]
    rescued = walk(nodes, [e("a", "h", "error")])
    assert by_id(rescued)["a"]["state"] == "failed" and rescued["outcome"]["status"] == "succeeded"
    assert rescued["problems"][0]["code"] == "dryrun_expression"
    ended = walk(nodes[:1], [])
    assert ended["outcome"]["message"].startswith("valeur illisible")


def test_a_transform_runs_on_sample_data_or_falls_back_to_an_empty_output():
    t = n("t", "transform", operation="nope")
    sc = walk([t], [])
    assert sc["outcome"]["status"] in ("succeeded", "failed") and by_id(sc)["t"]["state"] in ("simulated", "failed")


def test_the_run_stops_when_the_step_budget_or_the_deadline_is_reached():
    nodes = [n("a", "http"), n("b", "http"), n("c", "http")]
    edges = [e("a", "b"), e("b", "c")]
    sc = walk(nodes, edges, max_steps=2)
    assert sc["outcome"] == {"status": "stopped", "message": "Essai interrompu (durée ou nombre d'étapes maximal)."}
    assert [s["id"] for s in sc["steps"]] == ["a", "b"]
    assert walk(nodes, edges, deadline=time.monotonic() - 1)["steps"] == []


def test_expected_results_are_compared_with_what_was_produced():
    nodes = [n("q", "sql")]
    sc = walk(
        nodes, [], expected=[{"step": "q", "field": "count", "value": 5}, {"step": "q", "field": "nope", "value": 1}]
    )
    assert [p["code"] for p in sc["problems"]] == ["dryrun_unexpected"] and "attendu 5, obtenu 0" in sc["problems"][0][
        "message"
    ]


def test_an_ssh_step_uses_the_prepared_endpoints_and_a_forced_failure_follows_its_error_exit(monkeypatch):
    seen = {}

    def fake(node, ctx, endpoints, force_failure=False):
        seen["force"] = force_failure
        return {"status": "failed" if force_failure else "succeeded", "results": []}

    monkeypatch.setattr(dryrun.ssh_node, "dryrun_output", fake)
    nodes = [n("s", "ssh"), n("h", "smtp")]
    sc = walk(nodes, [e("s", "h", "error")], fail="s")
    assert seen["force"] is True and by_id(sc)["s"]["state"] == "simulated" and by_id(sc)["h"]["state"] == "simulated"
    assert walk(nodes[:1], [], fail="s")["outcome"]["message"] == "étape en échec"
    assert walk(nodes[:1], [])["steps"][0]["detail"].startswith("Commandes SSH simulées : aucune connexion")
