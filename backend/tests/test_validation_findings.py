"""Constats de `validate_graph` : un test par famille de défaut, pour que chaque message reste émis (et seulement à bon escient)."""

import pytest

from tests.wf_helpers import edge, node
from workflows.validation import MAX_NODES, validate_graph

IN = {"type": "object", "properties": {"n": {"type": "integer"}}}


def found(ws, nodes, edges=(), **extra):
    g = {"inputs": IN, "nodes": nodes, "edges": list(edges), "outputs": {}, **extra}
    return [(i["code"], i["level"]) for i in validate_graph(g, ws)]


def codes(ws, nodes, edges=(), **extra):
    return {c for c, _ in found(ws, nodes, edges, **extra)}


def one(ws, type_, **fields):
    return codes(ws, [node("a", type_, **fields)])


def test_malformed_graphs_stop_the_check(ws):
    assert validate_graph({"nodes": "x"}, ws)[0]["code"] == "graph_malformed"
    assert validate_graph({"nodes": [], "edges": 3}, ws)[0]["code"] == "graph_malformed"
    assert validate_graph({"nodes": ["x"]}, ws)[0]["code"] == "graph_malformed"
    too_many = [{"id": f"n{i}", "type": "join", "name": "x"} for i in range(MAX_NODES + 1)]
    assert validate_graph({"nodes": too_many}, ws)[0]["code"] == "too_many_nodes"


def test_ids_types_and_names(ws):
    bad = [{"id": "Mauvais Id", "type": "join", "name": "x"}, {"id": "ok", "type": "inconnu", "name": "x"}]
    assert {"bad_id", "bad_type"} <= codes(ws, bad)
    dup = [node("a", "wait", seconds=1), node("a", "wait", seconds=1)]
    assert "duplicate_id" in codes(ws, dup)
    assert ("missing_name", "warning") in found(ws, [{"id": "a", "type": "wait", "seconds": 1, "name": " "}])


def test_edges_and_entries(ws):
    a, b = node("a", "wait", seconds=1), node("b", "wait", seconds=1)
    assert "dangling_edge" in codes(ws, [a], [edge("a", "zz")])
    assert "cycle" in codes(ws, [a], [edge("a", "a")])
    assert "cycle" in codes(ws, [a, b], [edge("a", "b"), edge("b", "a")])
    assert "extra_start" in codes(ws, [a, b])


def test_flows_joins_and_exit_labels(ws):
    a, b, c = (node(x, "wait", seconds=1) for x in "abc")
    assert "implicit_join" in codes(ws, [a, b, c], [edge("a", "c"), edge("b", "c")])
    assert "weak_join" in codes(ws, [a, node("j", "join")], [edge("a", "j")])
    assert "weak_race" in codes(ws, [a, node("r", "race", losers="cancel")], [edge("a", "r")])
    assert "bad_race" in codes(ws, [a, b, node("r", "race", losers="zzz")], [edge("a", "r"), edge("b", "r")])
    assert "bad_branches" in codes(ws, [a, b], [edge("a", "b", "error")])  # a wait cannot have an error exit
    assert "bad_branches" in codes(ws, [a, b], [edge("a", "b", "bizarre")])
    cond = node("c", "condition", expr="inputs.n > 1")
    assert "bad_branches" in codes(ws, [cond, a], [edge("c", "a", "peut-être")])


def test_decision_wait_and_approval(ws):
    assert "missing_expr" in one(ws, "condition", expr=" ")
    assert "bad_wait" in one(ws, "wait", seconds=0)
    assert "bad_wait" in one(ws, "wait", seconds=5, resume="autre")
    assert "bad_wait" in one(ws, "wait", seconds=5, resume="form", form={"properties": {}})
    assert "missing_title" in one(ws, "approval", title=" ")


@pytest.mark.parametrize(
    ("type_", "fields", "code"),
    [
        ("http", {"method": "TRACE", "path": "/x", "connection": "c"}, "bad_http"),
        ("http", {"path": "x", "connection": "c"}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "body_format": "xml"}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "body_format": "file"}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "response_file": True, "pagination": {"type": "page"}}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "pagination": {"type": "zzz"}}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "pagination": {"type": "cursor"}}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "timeout_seconds": 999}, "bad_http"),
        ("http", {"path": "/x", "connection": "c", "mode": "zzz"}, "bad_http"),
        ("http", {"path": "/x"}, "missing_connection"),
        ("http", {"path": "/x", "connection": "absente"}, "unknown_connection"),
        ("http", {"integration": "nulle", "operation": "x", "connection": "c"}, "bad_http"),
        ("smtp", {"to": "", "subject": "", "body": ""}, "bad_smtp"),
        ("smtp", {"to": "a@b.c", "subject": "s", "body": "b"}, "missing_connection"),
        ("smtp", {"to": "a@b.c", "subject": "s", "body": "b", "connection": "x", "mode": "zzz"}, "bad_smtp"),
        ("sftp", {"operation": "zzz", "path": "", "connection": "x"}, "bad_sftp"),
        ("sftp", {"operation": "list", "path": "/", "mode": "zzz"}, "missing_connection"),
        ("ldap", {"filter": "(cn=x)", "base": "dc=x", "scope": "zzz", "mode": "zzz"}, "bad_ldap"),
        ("ldap", {"filter": "(cn=x)", "base": "dc=x"}, "missing_connection"),
        ("agent", {"goal": "g", "tools": [], "approval": "zzz", "mode": "zzz", "provider": "p"}, "bad_agent"),
        ("sql", {"statement": ""}, "bad_sql"),
        ("sql", {"statement": "select " + "x" * 9000}, "bad_sql"),
        ("sql", {"statement": "select 1", "connection": "x", "mode": "zzz"}, "bad_sql"),
        ("transform", {"operation": "join", "left": "a", "right": "b", "left_by": "", "right_by": ""}, "bad_transform"),
        ("transform", {"operation": "group", "aggregates": [{"fn": "nope"}], "by": "k"}, "bad_transform"),
        ("transform", {"operation": "limit", "count": "x"}, "bad_transform"),
    ],
)
def test_node_configuration_findings(ws, type_, fields, code):
    assert code in one(ws, type_, **fields)


def test_references_in_expressions(ws):
    cond = lambda e: codes(ws, [node("c", "condition", expr=e)])  # noqa: E731
    assert "bad_reference" in cond("run.zzz == 1")
    assert "bad_reference" in cond("steps.c.input")
    assert "bad_reference" in cond("autre.chose == 1")
    assert "bad_expression" in cond("inputs.n >")


def test_switch_variables_forms_and_compensation(ws):
    cases = [{"id": f"c{i}", "expr": "true"} for i in range(60)]
    assert "too_many_cases" in one(ws, "switch", cases=cases)
    assert "bad_variables" in codes(ws, [node("a", "wait", seconds=1)], variables="x")
    assert "bad_variable_type" in codes(
        ws, [node("a", "wait", seconds=1)], variables=[{"name": "v", "type": "zzz", "value": 1}]
    )
    assert "bad_input_form" in codes(ws, [node("a", "wait", seconds=1)], input_form={"sections": []})
    assert "script_too_large" in one(ws, "script", language="python", script="x=1\n" * 60000, outputs=[], bindings={})
    assert "bad_wait" in one(ws, "while", condition="true", delay_seconds=99999, workflow="x", version=1)
