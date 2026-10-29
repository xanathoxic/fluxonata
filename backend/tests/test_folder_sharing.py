import pytest

from runs.models import WorkflowRun
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    world["admin"] = make_user("ada-admin", ws, Role.ADMIN)
    world["erin"] = make_user("erin", ws, Role.DESIGNER)  # a designer with no grant
    return ws


def _wf(ws, world, slug, folder=""):
    v = publish(ws, slug, [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    Workflow.objects.filter(workspace=ws, slug=slug).update(folder=folder, in_catalog=True, mcp_exposed=True)
    return v


def _private(world, folder, grants):
    r = client_for(world["admin"]).put(f"{BASE}/folder-access/", {"folder": folder, "grants": grants}, format="json")
    assert r.status_code == 200, r.content
    return r


def test_a_private_folder_is_invisible_to_people_without_a_grant_everywhere(ws, world):
    v = _wf(ws, world, "paie", "RH")
    _wf(ws, world, "public")
    run = drain(launch(ws, v, world["designer"]))
    _private(world, "RH", [{"username": "dana", "level": "edit"}])
    erin, dana = client_for(world["erin"]), client_for(world["designer"])

    def names(r):
        return {w["slug"] for w in r.json()["results"]}

    assert names(erin.get(f"{BASE}/workflows/")) == {"public"} and names(dana.get(f"{BASE}/workflows/")) == {
        "paie",
        "public",
    }
    for url in (
        f"{BASE}/workflows/paie/",
        f"{BASE}/workflows/paie/versions/",
        f"{BASE}/workflows/paie/export/",
        f"{BASE}/workflows/paie/dependencies/",
        f"{BASE}/workflows/paie/audit/",
        f"{BASE}/workflows/paie/comments/",
        f"{BASE}/workflows/paie/contract/",
        f"{BASE}/workflow-runs/{run.pk}/",
        f"{BASE}/workflow-runs/{run.pk}/events/",
        f"{BASE}/workflow-runs/{run.pk}/files/",
        f"{BASE}/catalog/paie/",
    ):
        assert erin.get(url).status_code == 404, url
        assert dana.get(url).status_code == 200, url
    assert not {r["workflow"] for r in client_for(world["erin"]).get(f"{BASE}/workflow-runs/").json()["results"]} & {
        "paie"
    }
    assert len(dana.get(f"{BASE}/workflow-runs/").json()["results"]) == 1
    assert len(erin.get(f"{BASE}/workflow-runs/").json()["results"]) == 0
    assert {c["slug"] for c in erin.get(f"{BASE}/catalog/").json()["results"]} == {"public"}
    assert {w["slug"] for w in erin.get(f"{BASE}/workflows/export-all/").json()["workflows"]} == {"public"}
    assert "RH" not in erin.get(f"{BASE}/folders/", {"kind": "workflow"}).json()["results"]

    def tools(c):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        return {t["name"] for t in c.post(f"{BASE}/mcp/", body, format="json").json()["result"]["tools"]}

    assert tools(erin) == {"public"} and tools(dana) == {"paie", "public"}
    assert (
        erin.post(f"{BASE}/workflows/paie/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 404
    )
    assert erin.get(f"{BASE}/ops/dashboard/").status_code in (200, 403)
    # administrators always keep access
    assert client_for(world["admin"]).get(f"{BASE}/workflows/paie/").status_code == 200


def test_levels_only_narrow_the_role(ws, world):
    _wf(ws, world, "paie", "RH")
    vic = world["viewer"]
    _private(
        world,
        "RH",
        [
            {"username": "vic", "level": "edit"},
            {"username": "erin", "level": "view"},
            {"username": "dana", "level": "run"},
        ],
    )
    graph = {"graph": {"inputs": {"type": "object"}, "nodes": [], "edges": [], "outputs": {}}, "revision": 1}
    # erin (designer role) with a view grant: can read, cannot edit or publish or run
    erin = client_for(world["erin"])
    assert erin.get(f"{BASE}/workflows/paie/").json()["folder_level"] == "view"
    assert erin.put(f"{BASE}/workflows/paie/draft/", graph, format="json").status_code == 403
    assert erin.post(f"{BASE}/workflows/paie/publish/", {}, format="json").status_code == 403
    assert (
        erin.post(f"{BASE}/workflows/paie/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 403
    )
    assert erin.patch(f"{BASE}/workflows/paie/meta/", {"name": "x"}, format="json").status_code == 403
    # dana with a run grant can run, not edit
    dana = client_for(world["designer"])
    assert (
        dana.post(f"{BASE}/workflows/paie/runs/", {"environment": "test", "inputs": {}}, format="json").status_code
        == 202
    )
    assert dana.put(f"{BASE}/workflows/paie/draft/", graph, format="json").status_code == 403
    # vic is a VIEWER in the workspace: an « edit » grant does not make him an editor
    assert client_for(vic).put(f"{BASE}/workflows/paie/draft/", graph, format="json").status_code == 403
    # and nobody can create a workflow in, or move one into, a folder they cannot edit
    assert (
        erin.post(f"{BASE}/workflows/", {"slug": "n1", "name": "N1", "folder": "RH"}, format="json").status_code == 403
    )
    _wf(ws, world, "libre")
    assert (
        client_for(world["designer"])
        .patch(f"{BASE}/workflows/libre/meta/", {"folder": "RH"}, format="json")
        .status_code
        == 403
    )


def test_nearest_private_folder_wins_and_renames_carry_the_rules(ws, world):
    _wf(ws, world, "top", "RH")
    _wf(ws, world, "deep", "RH/Paie")
    _private(world, "RH", [{"username": "dana", "level": "edit"}])
    _private(world, "RH/Paie", [{"username": "erin", "level": "view"}])
    dana, erin = client_for(world["designer"]), client_for(world["erin"])
    assert (
        dana.get(f"{BASE}/workflows/top/").status_code == 200 and dana.get(f"{BASE}/workflows/deep/").status_code == 404
    )
    assert (
        erin.get(f"{BASE}/workflows/top/").status_code == 404 and erin.get(f"{BASE}/workflows/deep/").status_code == 200
    )
    assert {w["slug"] for w in erin.get(f"{BASE}/workflows/").json()["results"]} == {"deep"}
    r = client_for(world["admin"]).patch(
        f"{BASE}/folders/", {"kind": "workflow", "path": "RH", "new_path": "Social"}, format="json"
    )
    assert r.status_code == 200, r.content
    assert (
        erin.get(f"{BASE}/workflows/deep/").status_code == 200 and erin.get(f"{BASE}/workflows/top/").status_code == 404
    )
    rules = {a["folder"] for a in client_for(world["admin"]).get(f"{BASE}/folder-access/").json()["results"]}
    assert rules == {"Social", "Social/Paie"}
    # a person without access cannot rename or delete the folder
    assert erin.patch(
        f"{BASE}/folders/", {"kind": "workflow", "path": "Social", "new_path": "X"}, format="json"
    ).status_code in (403, 404)


def test_management_is_for_administrators_and_a_public_folder_is_public_again(ws, world):
    _wf(ws, world, "paie", "RH")
    assert (
        client_for(world["designer"])
        .put(f"{BASE}/folder-access/", {"folder": "RH", "grants": []}, format="json")
        .status_code
        == 403
    )
    bad = client_for(world["admin"]).put(
        f"{BASE}/folder-access/", {"folder": "RH", "grants": [{"username": "otto", "level": "view"}]}, format="json"
    )
    assert bad.status_code == 400  # not a member of the space
    _private(world, "RH", [])
    assert client_for(world["designer"]).get(f"{BASE}/workflows/paie/").status_code == 404
    assert client_for(world["admin"]).delete(f"{BASE}/folder-access/?folder=RH").status_code == 204
    assert client_for(world["designer"]).get(f"{BASE}/workflows/paie/").status_code == 200


def test_triggers_approvals_and_ai_context_follow_the_folder(ws, world):
    from ai.service import _callable_workflows

    _wf(ws, world, "paie", "RH")
    _wf(ws, world, "libre")
    _private(world, "RH", [{"username": "dana", "level": "edit"}])
    erin = client_for(world["erin"])
    body = {
        "name": "s",
        "workflow": "paie",
        "cron": "0 6 * * *",
        "timezone": "UTC",
        "environment": "test",
        "inputs": {},
    }
    assert erin.post(f"{BASE}/schedules/", body, format="json").status_code == 400  # unknown workflow for her
    assert client_for(world["designer"]).post(f"{BASE}/schedules/", body, format="json").status_code == 201
    assert erin.get(f"{BASE}/schedules/").json()["results"] == []
    assert len(client_for(world["designer"]).get(f"{BASE}/schedules/").json()["results"]) == 1
    # what the assistant is told about the workspace leaves out private workflows
    assert {w["slug"] for w in _callable_workflows(ws, "", world["erin"])} == {"libre"}
    assert {w["slug"] for w in _callable_workflows(ws, "", world["designer"])} == {"libre", "paie"}
    assert WorkflowRun.objects.count() == 0


def _routes(patterns, prefix=""):
    from django.urls import URLPattern, URLResolver

    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _routes(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern)


def test_sweep_no_get_route_mentions_a_hidden_workflow_or_its_run(ws, world):
    """Every GET route of the API, asked by someone without access, must neither name the private workflow nor expose its
    run (by name, id or file), whatever the route: a safety net for routes added later."""
    import re

    from django.urls import get_resolver

    from runs import files

    v = _wf(ws, world, "paie-secrete", "RH")
    run = drain(launch(ws, v, world["designer"]))
    stub = files.save(run, "a", "bulletin.pdf", "application/pdf", b"%PDF secret")
    _private(world, "RH", [{"username": "dana", "level": "edit"}])
    erin = client_for(world["erin"])
    granted = client_for(world["designer"])
    values = {
        "workspace": "team-a",
        "slug": "paie-secrete",
        "pk": str(run.pk),
        "kind": "workflow",
        "node": "a",
        "version": "1.0.0",
    }
    checked, leaks = 0, []
    for route in sorted(set(_routes(get_resolver().url_patterns))):
        if not route.startswith("api/v1/workspaces/"):
            continue
        for pk in (str(run.pk), stub["file_id"], "1"):
            url = "/" + re.sub(
                r"<(?:\w+:)?(\w+)>", lambda m, pk=pk: pk if m.group(1) == "pk" else values.get(m.group(1), "x"), route
            )
            url = url.removeprefix("//")
            r = erin.get(url)
            checked += 1
            body = r.content.decode("utf-8", "ignore")
            if r.status_code == 200 and (
                "paie-secrete" in body or str(run.pk) in body or "bulletin.pdf" in body or "PAIE-SECRETE" in body
            ):
                leaks.append(url)
    assert checked > 100 and not leaks, leaks
    # the sweep does detect exposure: the same sweep as a granted person finds the workflow in many routes
    seen = 0
    for route in sorted(set(_routes(get_resolver().url_patterns))):
        if route.startswith("api/v1/workspaces/"):
            url = "/" + re.sub(
                r"<(?:\w+:)?(\w+)>", lambda m: str(run.pk) if m.group(1) == "pk" else values.get(m.group(1), "x"), route
            )
            body = granted.get(url.removeprefix("//")).content.decode("utf-8", "ignore")
            seen += "paie-secrete" in body or str(run.pk) in body
    assert seen >= 8, seen


def test_sweep_nothing_can_be_changed_on_a_hidden_workflow_or_run(ws, world):
    import re

    from django.urls import get_resolver

    v = _wf(ws, world, "paie-secrete", "RH")
    run = drain(launch(ws, v, world["designer"]))
    _private(world, "RH", [{"username": "dana", "level": "edit"}])
    erin = client_for(world["erin"])
    values = {
        "workspace": "team-a",
        "slug": "paie-secrete",
        "pk": str(run.pk),
        "kind": "workflow",
        "node": "a",
        "version": "1.0.0",
    }
    done, wrong = 0, []
    for route in sorted(set(_routes(get_resolver().url_patterns))):
        if not route.startswith("api/v1/workspaces/") or not re.search(
            r"<(?:\w+:)?(?:slug|pk|kind)>", route.split("workspaces/")[1].split("/", 1)[1]
        ):
            continue
        url = ("/" + re.sub(r"<(?:\w+:)?(\w+)>", lambda m: values.get(m.group(1), "x"), route)).removeprefix("//")
        if "/executions/" in url or "/actions/" in url or "/skills/" in url or "/ai/" in url:
            continue  # not about workflows
        for method in ("post", "put", "patch", "delete"):
            r = getattr(erin, method)(url, {}, format="json")
            done += 1
            if 200 <= r.status_code < 300 and not (method == "delete" and "/favorites/" in url):  # one's own bookmark
                wrong.append((method, url, r.status_code))
    assert done > 50 and not wrong, wrong
    from workflows.models import Workflow

    assert Workflow.objects.filter(slug="paie-secrete").exists()
