import pytest

from tests.wf_helpers import ACTIONS, drain, edge, launch, make_action, node, publish, schema
from workflows import transforms
from workflows.expressions import evaluate
from workflows.transforms import TransformError, apply

ROWS = [
    {"id": 1, "service": "web", "montant": 10, "nom": "b"},
    {"id": 2, "service": "db", "montant": 30, "nom": "a"},
    {"id": 3, "service": "web", "montant": 50, "nom": "c"},
    {"id": 4, "service": "web", "montant": 10, "nom": "b"},
]
CTX = {
    "inputs": {"rows": ROWS, "tags": [{"id": 1, "owner": "ada"}, {"id": 3, "owner": "bob"}]},
    "vars": {},
    "steps": {},
}


def run(**node):
    return apply({"id": "t", "type": "transform", **node}, CTX)


def test_set_computes_fields():
    out = run(
        op="set",
        fields={
            "total": "{{ sum(inputs.rows[0].montant + 1) }}".replace("sum(", "(").replace(")}}", ") }}"),
            "label": "x-{{ inputs.rows[1].nom }}",
        },
    )
    assert out == {"total": 11, "label": "x-a"}


def test_map_filter_sort_limit():
    assert run(op="map", items="inputs.rows", fields={"n": "{{ item.nom }}", "ttc": "{{ item.montant * 1.2 }}"})[
        "items"
    ][0] == {"n": "b", "ttc": 12.0}
    out = run(op="filter", items="inputs.rows", where="item.montant > 10 and item.service == 'web'")
    assert [r["id"] for r in out["items"]] == [3] and out["count"] == 1
    assert [r["id"] for r in run(op="sort", items="inputs.rows", by="item.montant", order="desc")["items"]][:2] == [
        3,
        2,
    ]
    assert [r["id"] for r in run(op="sort", items="inputs.rows", by="item.nom")["items"]] == [2, 1, 4, 3]
    assert [r["id"] for r in run(op="limit", items="inputs.rows", skip=1, count=2)["items"]] == [2, 3]


def test_unique_and_group_with_aggregates():
    assert [r["id"] for r in run(op="unique", items="inputs.rows", by="item.service")["items"]] == [1, 2]
    out = run(
        op="group",
        items="inputs.rows",
        by="item.service",
        aggregates=[
            {"name": "total", "fn": "sum", "expr": "item.montant"},
            {"name": "moy", "fn": "avg", "expr": "item.montant"},
            {"name": "noms", "fn": "list", "expr": "item.nom"},
        ],
    )
    web = next(g for g in out["items"] if g["key"] == "web")
    assert web["count"] == 3 and web["total"] == 70 and round(web["moy"], 2) == 23.33 and web["noms"] == ["b", "c", "b"]


def test_merge_inner_left_append():
    inner = run(op="merge", items="inputs.rows", right="inputs.tags", left_by="item.id", right_by="item.id")
    assert [(r["id"], r["owner"]) for r in inner["items"]] == [(1, "ada"), (3, "bob")]
    left = run(
        op="merge", items="inputs.rows", right="inputs.tags", left_by="item.id", right_by="item.id", merge_mode="left"
    )
    assert left["count"] == 4 and "owner" not in left["items"][1]
    assert run(op="merge", items="inputs.rows", right="inputs.tags", merge_mode="append")["count"] == 6


@pytest.mark.parametrize(
    "node",
    [
        {"op": "nope"},
        {"op": "filter", "items": "inputs.rows"},
        {"op": "filter", "items": "inputs.nothing", "where": "true"},
        {"op": "filter", "items": "inputs.rows", "where": "item.montant > 'x'"},
        {"op": "sort", "items": "inputs.rows", "by": "item.id + 0 + (0 - 0) - item.nom"},
        {
            "op": "group",
            "items": "inputs.rows",
            "by": "item.service",
            "aggregates": [{"name": "t", "fn": "sum", "expr": "item.nom"}],
        },
        {"op": "limit", "items": "inputs.rows", "count": -1},
    ],
)
def test_bad_transforms_raise_french_errors(node):
    with pytest.raises(TransformError):
        run(**node)


def test_dates():
    assert evaluate("date_add('2026-01-31T10:00:00+00:00', 1, 'days')", CTX) == "2026-02-01T10:00:00+00:00"
    assert evaluate("date_diff('2026-01-03', '2026-01-01')", CTX) == 2
    assert evaluate("date_diff('2026-01-01T12:00:00Z', '2026-01-01T00:00:00Z', 'hours')", CTX) == 12
    assert evaluate("date_format('2026-03-05T08:00:00Z', '%d/%m/%Y')", CTX) == "05/03/2026"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def test_a_transform_step_runs_in_a_workflow_and_feeds_the_next_step(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "tr",
        [
            node("f", "transform", op="filter", items="inputs.rows", where="item.montant >= 30"),
            node("e", action="echo", inputs={"value": "{{ steps.f.output.count }} / {{ run.environment }}"}),
        ],
        [edge("f", "e")],
        inputs=schema(rows={"type": "array"}),
        user=user,
    )
    r = drain(launch(ws, v, user, inputs={"rows": ROWS}))
    assert r.status == "succeeded" and r.steps.get(node_id="e").output == {"value": "2 / test"}


def test_a_failing_transform_is_a_failed_step_with_a_clear_code(ws, world):
    user = world["designer"]
    v = publish(
        ws,
        "tr2",
        [node("f", "transform", op="filter", items="inputs.rows", where="item.nom > 5")],
        [],
        inputs=schema(rows={"type": "array"}),
        user=user,
    )
    r = drain(launch(ws, v, user, inputs={"rows": ROWS}))
    assert r.status == "failed" and r.error["code"] == "TRANSFORM_ERROR"


def test_validator_checks_transform_fields_and_the_item_root(ws, world):
    from workflows.validation import validate_graph

    def codes(**f):
        g = {"inputs": schema(rows={"type": "array"}), "nodes": [node("f", "transform", **f)], "edges": []}
        return {i["code"] for i in validate_graph(g, ws, "t") if i["level"] == "error"}

    assert codes(op="filter", items="inputs.rows", where="item.montant > 3") == set()
    assert "bad_transform" in codes(op="filter", items="inputs.rows")
    assert "bad_transform" in codes(op="oops")
    assert "unknown_input" in codes(op="filter", items="inputs.absent", where="true")
    assert "bad_reference" in codes(op="filter", items="item.rows", where="true")  # `item` only inside per-item fields


def test_dry_run_executes_transforms_on_sample_data(ws, world):
    assert transforms.output_fields({"op": "filter"}) == {"items": {"type": "array"}, "count": {"type": "integer"}}


def test_split_and_chunk():
    ctx = {**CTX, "inputs": {**CTX["inputs"], "txt": " a, b ,,c "}}
    assert apply({"op": "split", "text": "inputs.txt"}, ctx) == {"items": ["a", "b", "c"], "count": 3}
    assert apply({"op": "split", "text": "inputs.txt", "separator": ";"}, ctx)["items"] == ["a, b ,,c"]
    assert apply({"op": "split", "text": "'x\\ny'", "separator": "\\n"}, ctx)["items"] == ["x", "y"]
    out = apply({"op": "chunk", "items": "inputs.rows", "size": 3}, ctx)
    assert out["count"] == 2 and [len(c) for c in out["items"]] == [3, 1]
    for bad in (
        {"op": "chunk", "items": "inputs.rows", "size": 0},
        {"op": "split", "text": "inputs.rows"},
        {"op": "split", "text": "'a'", "separator": ""},
    ):
        with pytest.raises(TransformError):
            apply(bad, ctx)
