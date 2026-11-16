"""Caractérisation de `workflows.validation.always_run_map` : pour chaque étape, les étapes dont la sortie existe À COUP SÛR quand elle s'exécute.

Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après. Les graphes sont minimaux et lus à la main."""

from workflows.validation import MAX_SCENARIO_VARS, _dominators, always_run_map


def n(nid, type_="action", **kw):
    return {"id": nid, "type": type_, **kw}


def e(a, b, label=None):
    return {"from": a, "to": b, **({"label": label} if label else {})}


def certain(nodes, edges, order=None):
    return always_run_map(nodes, edges, order or [x["id"] for x in nodes])


def test_in_a_chain_every_step_is_certain_whenever_any_of_them_runs():
    # the map answers « which outputs exist whenever this step runs » : later steps of a straight chain count too
    out = certain([n("a"), n("b"), n("c")], [e("a", "b"), e("b", "c")])
    assert out == {"a": {"a", "b", "c"}, "b": {"a", "b", "c"}, "c": {"a", "b", "c"}}


def test_a_decision_branch_is_not_certain_after_the_join():
    nodes = [n("a"), n("c", "condition", expr="inputs.x > 1"), n("yes"), n("no"), n("j", "join"), n("z")]
    edges = [e("a", "c"), e("c", "yes", "true"), e("c", "no", "false"), e("yes", "j"), e("no", "j"), e("j", "z")]
    out = certain(nodes, edges)
    assert out["z"] == {"z", "j", "c", "a"}  # neither `yes` nor `no`: only one of them ran
    assert out["yes"] == {"yes", "c", "a", "j", "z"}  # when `yes` runs, the join and what follows run as well


def test_two_decisions_with_the_same_expression_are_the_same_decision():
    nodes = [
        n("c1", "condition", expr="inputs.x  >  1"),
        n("p"),
        n("c2", "condition", expr="inputs.x > 1"),
        n("q"),
    ]
    edges = [e("c1", "p", "true"), e("p", "c2"), e("c2", "q", "true")]
    assert certain(nodes, edges)["q"] == {"q", "p", "c2", "c1"}
    assert certain(nodes, edges)["c1"] == {"c1"}  # when the decision is false nothing else runs


def test_parallel_branches_all_run_so_both_are_certain_after_the_join():
    nodes = [n("a"), n("b"), n("c"), n("j", "join"), n("d")]
    edges = [e("a", "b"), e("a", "c"), e("b", "j"), e("c", "j"), e("j", "d")]
    assert certain(nodes, edges)["d"] == {"a", "b", "c", "j", "d"}


def test_a_switch_branch_is_certain_only_in_its_own_case():
    nodes = [
        n("s", "switch", cases=[{"id": "x", "expr": "1 == 1"}, {"id": "y", "expr": "1 == 2"}]),
        n("px"),
        n("py"),
        n("pd"),
        n("j", "join"),
    ]
    edges = [e("s", "px", "x"), e("s", "py", "y"), e("s", "pd", "default"), e("px", "j"), e("py", "j"), e("pd", "j")]
    out = certain(nodes, edges)
    assert out["j"] == {"j", "s"}
    assert out["px"] == {"px", "s", "j"}


def test_a_step_with_an_error_exit_may_fail_so_what_follows_normally_is_not_certain_after_the_recovery():
    nodes = [n("a"), n("b"), n("rec"), n("j", "join")]
    edges = [e("a", "b"), e("a", "rec", "error"), e("b", "j"), e("rec", "j")]
    out = certain(nodes, edges)
    assert out["j"] == {"j"}  # `a` may have failed, `b` ran only if it did not, `rec` only if it did
    assert out["b"] == {"a", "b", "j"}


def test_a_branch_that_ends_in_an_exception_is_a_run_that_fails_anyway():
    nodes = [n("c", "condition", expr="inputs.x"), n("ok"), n("t", "throw"), n("z")]
    edges = [e("c", "ok", "true"), e("c", "t", "false"), e("ok", "z")]
    out = certain(nodes, edges)
    assert out["z"] == {"z", "ok", "c"}  # the failing scenario does not count


def test_a_step_that_only_runs_in_a_run_that_fails_has_no_guarantee_at_all():
    nodes = [n("c", "condition", expr="inputs.x"), n("w"), n("t", "throw"), n("ok")]
    edges = [e("c", "ok", "true"), e("c", "w", "false"), e("w", "t")]
    order = ["c", "ok", "w", "t"]
    out = always_run_map(nodes, edges, order)
    assert out["w"] == set(order)  # never runs without the run ending in an exception: the cautious answer


def test_after_a_race_the_slower_branches_are_not_certain():
    nodes = [n("a"), n("b"), n("c"), n("r", "race"), n("z")]
    edges = [e("a", "b"), e("a", "c"), e("b", "r"), e("c", "r"), e("r", "z")]
    out = certain(nodes, edges)
    assert out["z"] == {"z", "r", "a"}  # neither `b` nor `c` is certain: only the winner ran to its end


def test_too_many_open_choices_fall_back_to_the_cruder_rule_of_dominators():
    count = MAX_SCENARIO_VARS + 1
    nodes, edges, prev = [], [], None
    for i in range(count):
        nodes.append(n(f"c{i}", "condition", expr=f"inputs.v{i}"))
        if prev:
            edges.append(e(prev, f"c{i}", "true"))
        prev = f"c{i}"
    order = [x["id"] for x in nodes]
    assert always_run_map(nodes, edges, order) == _dominators(edges, order)
