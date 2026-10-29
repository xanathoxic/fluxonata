import pytest

from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish
from workflows.validation import validate_graph


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def _race(ws, world, slug="course", **race):
    """A quick action against a one-hour wait: the action wins, a step after the race reads who arrived first."""
    return publish(
        ws,
        slug,
        [
            node("fast", action="echo", inputs={"value": "vite"}),
            node("slow", "wait", seconds=3600),
            node("r", "race", **race),
            node("after", "wait", seconds=1),
        ],
        [edge("fast", "r"), edge("slow", "r"), edge("r", "after")],
        outputs={"gagnant": "{{ steps.r.output.winner }}", "valeur": "{{ steps.r.output.value }}"},
        user=world["designer"],
    )


def test_first_branch_to_arrive_wins_and_the_slower_one_is_cancelled(ws, world):
    run = drain(
        launch(ws, _race(ws, world), world["designer"]),
        max_rounds=100,
        until=lambda r: r.steps.filter(node_id="r").exists(),
    )
    steps = {s.node_id: s for s in run.steps.all()}
    assert steps["r"].status == "succeeded" and steps["r"].output["winner"] == "fast"
    assert steps["r"].output["winner_name"] == "FAST" and steps["r"].output["value"]["value"] == "vite"
    assert steps["slow"].status == "cancelled"  # the hour-long wait does not hold the run
    run = drain(run, max_rounds=400)
    assert run.status == "succeeded" and run.outputs["gagnant"] == "fast"


def test_losers_can_be_left_to_finish(ws, world):
    v = _race(ws, world, slug="laisser", losers="finish")
    run = drain(
        launch(ws, v, world["designer"]), max_rounds=100, until=lambda r: r.steps.filter(node_id="after").exists()
    )
    slow = run.steps.get(node_id="slow")
    assert run.steps.get(node_id="r").output["winner"] == "fast" and slow.status == "waiting"
    assert run.status == "running"  # it will end when the slower branch does


def test_validation_of_the_race_step(ws, world):
    def codes(nodes, edges):
        g = {"inputs": {"type": "object"}, "nodes": nodes, "edges": edges, "outputs": {}}
        return {i["code"] for i in validate_graph(g, ws, "t")}

    a, b = node("a", action="echo", inputs={"value": "x"}), node("b", action="echo", inputs={"value": "y"})
    ok = codes([a, b, node("r", "race")], [edge("a", "r"), edge("b", "r")])
    assert not ({"implicit_join", "weak_race", "bad_race"} & ok)
    assert "weak_race" in codes([a, node("r", "race")], [edge("a", "r")])
    assert "bad_race" in codes([a, b, node("r", "race", losers="x")], [edge("a", "r"), edge("b", "r")])
    assert "implicit_join" in codes([a, b, node("r", "wait", seconds=1)], [edge("a", "r"), edge("b", "r")])
    # reading a branch-only step after the race is the same hazard as after a join
    g = {
        "inputs": {"type": "object"},
        "nodes": [
            a,
            b,
            node("r", "race"),
            node("n", action="echo", inputs={"value": "{{ steps.a.output.value }}{{ steps.r.output.winner }}"}),
        ],
        "edges": [edge("a", "r"), edge("b", "r"), edge("r", "n")],
        "outputs": {},
    }
    found = validate_graph(g, ws, "t")
    skipped = {i["ref"] for i in found if i["code"] == "maybe_skipped"}
    assert skipped == {"a"}  # the loser may have been cancelled; the race's own output is always there
