"""Systematic cross-team isolation check.

Every route under /api/v1/workspaces/<workspace>/... is exercised against `team-a` by (1) an anonymous client and
(2) a user who belongs ONLY to `team-b`, with every HTTP method and plausible object ids. Nothing may succeed and
nothing may reveal that the target exists (the answer is 404, never 403/200). New routes are covered automatically.
"""

import re
import uuid

import pytest
from django.urls import get_resolver
from rest_framework.test import APIClient

from tests.conftest import client_for
from tests.wf_helpers import make_action, node, publish

DUMMIES = {"slug": "echo", "uuid": str(uuid.uuid4()), "int": "1", "str": "demo"}
METHODS = ["get", "post", "put", "patch", "delete"]
WS_PREFIX = re.compile(r"^api/v1/workspaces/<slug:workspace>/")


def workspace_routes():
    routes = []

    def walk(patterns, prefix=""):
        for p in patterns:
            part = prefix + str(p.pattern)
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, part)
            elif WS_PREFIX.match(part):
                routes.append(part)

    walk(get_resolver().url_patterns)
    return sorted(set(routes))


def fill(route: str, workspace: str, ids: dict) -> str:
    def repl(m):
        kind, name = (m.group(1) or "str"), m.group(2)
        if name == "workspace":
            return workspace
        return ids.get(name) or DUMMIES.get(kind, "x")

    return "/" + re.sub(r"<(?:(\w+):)?(\w+)>", repl, route)


@pytest.fixture
def populated(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(ws, "echo", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    return world


def test_there_are_many_workspace_routes():
    assert len(workspace_routes()) > 60  # guards against the introspection silently finding nothing


@pytest.mark.parametrize("route", workspace_routes())
def test_outsider_and_anonymous_get_nothing_on_any_workspace_route(route, populated):
    url = fill(route, "team-a", {"name": "echo", "username": "dana", "version": "1.0.0", "node": "a"})
    outsider, anon = client_for(populated["outsider"]), APIClient()
    for method in METHODS:
        r = getattr(outsider, method)(url, {}, format="json")
        assert r.status_code in (404, 405), f"{method.upper()} {url} -> {r.status_code} for a non-member"
        a = getattr(anon, method)(url, {}, format="json")
        assert a.status_code in (401, 403, 405), f"{method.upper()} {url} -> {a.status_code} for anonymous"


def test_member_of_both_teams_cannot_use_team_a_ids_under_team_b(populated, world):
    """Right object id, wrong workspace in the URL: still nothing (the id alone grants nothing)."""
    from runs import engine
    from tenancy.models import Environment, Membership, Role

    ws_a = world["ws_a"]
    Membership.objects.create(user=world["outsider"], workspace=ws_a, role=Role.DESIGNER)  # now a member of both
    run, _ = engine.start_run(
        workspace=ws_a,
        environment=Environment.objects.get(workspace=ws_a, kind="test"),
        workflow_version=ws_a.workflows.get(slug="echo").latest_version(),
        user=world["designer"],
        inputs={},
    )
    c = client_for(world["outsider"])
    assert c.get(f"/api/v1/workspaces/team-a/workflow-runs/{run.id}/").status_code == 200  # legitimate
    assert c.get(f"/api/v1/workspaces/team-b/workflow-runs/{run.id}/").status_code == 404  # same id, other workspace
    assert c.post(f"/api/v1/workspaces/team-b/workflow-runs/{run.id}/cancel/").status_code == 404
