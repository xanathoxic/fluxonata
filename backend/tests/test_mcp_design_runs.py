"""MCP de conception : suivre les exécutions et DEMANDER un lancement. L'assistant ne lance jamais : une personne connectée confirme (dev ou recette seulement)."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from audit.models import AuditEvent
from runs.models import RunRequest, WorkflowRun
from tenancy.models import Environment, Role
from tests.conftest import client_for, make_user
from tests.test_mcp_design_write import call
from tests.wf_helpers import launch, node, publish, schema
from workflows.mcp_design import TOOLS, all_tools

DEV_ONLY_TOOLS = {"list_runs", "get_run", "list_run_requests", "get_run_request"}


@pytest.fixture
def ws(world):
    ws = world["ws_a"]
    Environment.objects.get_or_create(workspace=ws, kind="dev", defaults={"name": "dev"})
    return ws


@pytest.fixture
def version(ws, world):
    return publish(
        ws,
        "salue",
        [node("a", action="text-stats", inputs={"text": "{{inputs.nom}}"})],
        [],
        inputs=schema(["nom"], nom={"type": "string"}),
        user=world["designer"],
    )


def token_client(user, ws, scopes=("view", "edit")):
    user._token = SimpleNamespace(scopes=list(scopes), workspace_id=ws.id, workspace=ws, environments=[])
    return client_for(user)


@pytest.fixture
def c(world, ws):
    user = world["designer"]
    yield token_client(user, ws)
    del user._token


def human(user):
    """La même personne en session d'interface : un objet utilisateur NEUF (celui du jeton porte `_token`)."""
    return client_for(type(user).objects.get(pk=user.pk))


def ask(c, env="test", **over):
    return call(
        c, "request_run", {"slug": "salue", "environment": env, "inputs": {"nom": "Ada"}, "reason": "Vérifier", **over}
    )


def test_the_reading_tools_are_read_only_and_the_request_tool_is_listed():
    for name in DEV_ONLY_TOOLS:
        assert TOOLS[name][0]["annotations"]["readOnlyHint"] is True
    assert "request_run" in all_tools() and all_tools()["request_run"][0]["annotations"]["readOnlyHint"] is False
    assert not [n for n in all_tools() if n in ("run_workflow", "start_run", "confirm_run", "launch_workflow")]


def test_a_request_creates_no_run_and_waits_for_a_person(ws, world, version, c):
    before = WorkflowRun.objects.count()
    res, data = ask(c)
    assert not res["isError"] and data["status"] == "pending" and data["environment"] == "test"
    assert WorkflowRun.objects.count() == before  # rien n'est lancé tant qu'une personne n'a pas confirmé
    req = RunRequest.objects.get()
    assert req.status == "pending" and req.inputs == {"nom": "Ada"} and req.requested_by == world["designer"]
    assert AuditEvent.objects.filter(action="run_request.create", data__external_assistant=True).count() == 1
    _, again = call(c, "get_run_request", {"request_id": data["request_id"]})
    assert again["status"] == "pending" and again["run_id"] is None


def test_production_is_refused_at_the_request_even_with_a_valid_workflow(ws, world, version, c):
    res, _ = ask(c, env="prod")
    assert res["isError"] is True and RunRequest.objects.count() == 0


def test_dev_and_test_are_accepted_and_bad_inputs_are_explained(ws, world, version, c):
    assert not ask(c, env="dev")[0]["isError"]
    res, _ = call(c, "request_run", {"slug": "salue", "environment": "test", "inputs": {}, "reason": "x"})
    assert res["isError"] is True and "nom" in res["content"][0]["text"]  # champ obligatoire absent : dit lequel
    assert call(c, "request_run", {"slug": "inconnu", "environment": "test", "reason": "x"})[0]["isError"] is True


def test_a_viewer_cannot_ask_and_a_hidden_workflow_looks_unknown(ws, world, version):
    viewer = world["viewer"]
    cv = token_client(viewer, ws, scopes=("view",))
    assert ask(cv)[0]["isError"] is True and RunRequest.objects.count() == 0  # son rôle ne permet pas de lancer
    del viewer._token


def test_only_five_requests_may_wait_at_once(ws, world, version, c):
    for _ in range(5):
        assert not ask(c)[0]["isError"]
    res, _ = ask(c)
    assert res["isError"] is True and RunRequest.objects.count() == 5


def confirm_url(ws, req):
    return f"/api/v1/workspaces/{ws.slug}/run-requests/{req.id}/confirm/"


def test_a_person_confirms_and_the_run_is_started_in_their_name(ws, world, version, c):
    ask(c)
    req = RunRequest.objects.get()
    person = human(world["designer"])  # session humaine : pas de jeton
    resp = person.post(confirm_url(ws, req), {"comment": "ok"}, format="json")
    assert resp.status_code == 200, resp.content
    req.refresh_from_db()
    run = WorkflowRun.objects.get()
    assert req.status == "launched" and req.run_id == run.id and run.environment.kind == "test"
    assert run.requested_by == world["designer"] and run.inputs == {"nom": "Ada"}
    assert AuditEvent.objects.filter(action="run_request.confirm", data__run=str(run.id)).exists()
    # rejouer la confirmation ne crée pas un second run
    assert person.post(confirm_url(ws, req), {}, format="json").status_code == 200
    assert WorkflowRun.objects.count() == 1
    _, got = call(c, "get_run_request", {"request_id": str(req.id)})
    assert got["status"] == "launched" and got["run_id"] == str(run.id)


def test_a_token_can_never_confirm_or_refuse(ws, world, version, c):
    ask(c)
    req = RunRequest.objects.get()
    assert c.post(confirm_url(ws, req), {}, format="json").status_code == 403
    assert c.post(confirm_url(ws, req).replace("confirm", "refuse"), {}, format="json").status_code == 403
    assert WorkflowRun.objects.count() == 0 and RunRequest.objects.get().status == "pending"


def test_a_viewer_session_cannot_confirm(ws, world, version, c):
    ask(c)
    req = RunRequest.objects.get()
    assert human(world["viewer"]).post(confirm_url(ws, req), {}, format="json").status_code == 403
    assert WorkflowRun.objects.count() == 0


def test_an_expired_request_is_never_launched(ws, world, version, c):
    ask(c)
    RunRequest.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    resp = human(world["designer"]).post(confirm_url(ws, RunRequest.objects.get()), {}, format="json")
    assert resp.status_code == 400 and WorkflowRun.objects.count() == 0
    assert RunRequest.objects.get().status == "expired"


def test_a_refused_request_stays_refused(ws, world, version, c):
    ask(c)
    req = RunRequest.objects.get()
    person = human(world["designer"])
    assert (
        person.post(confirm_url(ws, req).replace("confirm", "refuse"), {"comment": "non"}, format="json").status_code
        == 200
    )
    assert person.post(confirm_url(ws, req), {}, format="json").status_code == 400
    assert WorkflowRun.objects.count() == 0
    _, got = call(c, "get_run_request", {"request_id": str(req.id)})
    assert got["status"] == "refused" and got["comment"] == "non"


def test_a_request_pointing_at_production_is_not_launched_even_if_forged(ws, world, version, c):
    ask(c)
    RunRequest.objects.update(environment=Environment.objects.get(workspace=ws, kind="prod"))
    resp = human(world["designer"]).post(confirm_url(ws, RunRequest.objects.get()), {}, format="json")
    assert resp.status_code == 403 and WorkflowRun.objects.count() == 0


def test_the_other_workspace_cannot_see_or_decide(ws, world, version, c):
    ask(c)
    req = RunRequest.objects.get()
    assert human(world["outsider"]).post(confirm_url(ws, req), {}, format="json").status_code == 404


def test_the_list_endpoint_shows_pending_requests_to_people_who_can_decide(ws, world, version, c):
    ask(c)
    rows = human(world["designer"]).get(f"/api/v1/workspaces/{ws.slug}/run-requests/?status=pending").json()
    rows = rows["results"] if isinstance(rows, dict) else rows
    assert len(rows) == 1 and rows[0]["can_decide"] is True and rows[0]["inputs"] == {"nom": "Ada"}
    assert rows[0]["workflow"]["slug"] == "salue" and rows[0]["reason"] == "Vérifier"
    viewer_rows = human(world["viewer"]).get(f"/api/v1/workspaces/{ws.slug}/run-requests/").json()
    viewer_rows = viewer_rows["results"] if isinstance(viewer_rows, dict) else viewer_rows
    assert viewer_rows[0]["can_decide"] is False


def test_runs_can_be_listed_and_followed_with_their_steps(ws, world, version, c):
    run = launch(ws, version, world["designer"], {"nom": "Ada"})
    _, listing = call(c, "list_runs", {"workflow": "salue"})
    assert listing["results"][0]["run_id"] == str(run.id) and listing["results"][0]["environment"] == "test"
    _, none = call(c, "list_runs", {"status": "cancelled"})
    assert none["count"] == 0
    res, got = call(c, "get_run", {"run_id": str(run.id)})
    assert not res["isError"] and got["inputs"] == {"nom": "Ada"} and got["finished"] is False
    assert [s["step"] for s in got["steps"]] == [s.node_id for s in run.steps.order_by("started_at", "id")]
    assert call(c, "get_run", {"run_id": "pas-un-uuid"})[0]["isError"] is True


def test_a_step_output_is_clipped_and_available_in_full_on_demand(ws, world, version, c):
    run = launch(ws, version, world["designer"], {"nom": "Ada"})
    step = run.steps.first() or run.steps.create(node_id="a", node_type="action")
    step.output = {"texte": "x" * 5000}
    step.status = "succeeded"
    step.save()
    _, short = call(c, "get_run", {"run_id": str(run.id)})
    s = next(s for s in short["steps"] if s["step"] == step.node_id)
    assert s["output_truncated"] is True and len(s["output"]) <= 2000
    _, full = call(c, "get_run", {"run_id": str(run.id), "step": step.node_id})
    assert full["steps"][0]["output"] == {"texte": "x" * 5000} and full["steps"][0]["output_truncated"] is False
    assert call(c, "get_run", {"run_id": str(run.id), "step": "nope"})[0]["isError"] is True


def test_a_run_in_a_private_folder_is_invisible_to_the_assistant(ws, world, version, c):
    from workflows.models import FolderAccess

    run = launch(ws, version, world["designer"], {"nom": "Ada"})
    version.workflow.folder = "Secret"
    version.workflow.save()
    FolderAccess.objects.create(workspace=ws, folder="Secret")
    assert call(c, "get_run", {"run_id": str(run.id)})[0]["isError"] is True
    assert call(c, "list_runs")[1]["count"] == 0
    ask_res, _ = ask(c)
    assert ask_res["isError"] is True and RunRequest.objects.count() == 0


def test_get_run_never_exposes_a_resume_url(ws, world, version, c):
    run = launch(ws, version, world["designer"], {"nom": "Ada"})
    res, _ = call(c, "get_run", {"run_id": str(run.id)})
    assert "resume" not in res["content"][0]["text"] and "/r/" not in res["content"][0]["text"]


def test_another_person_does_not_see_my_requests(ws, world, version, c):
    ask(c)
    other = make_user("olga", ws, Role.DESIGNER)
    co = token_client(other, ws)
    assert call(co, "list_run_requests")[1]["count"] == 0
    assert call(co, "get_run_request", {"request_id": str(RunRequest.objects.get().id)})[0]["isError"] is True
    del other._token


# ---- validation automatique (activée par un administrateur de l'espace)
def auto_url(ws):
    return f"/api/v1/workspaces/{ws.slug}/run-requests/auto/"


@pytest.fixture
def admin(world, ws):
    return make_user("adele", ws, Role.ADMIN)


def enable(ws, admin_user):
    r = human(admin_user).put(auto_url(ws), {"enabled": True}, format="json")
    assert r.status_code == 200, r.content
    return r


def test_the_automatic_validation_is_off_by_default_and_a_request_still_waits(ws, world, version, c):
    assert human(world["designer"]).get(auto_url(ws)).json() == {
        "enabled": False,
        "unrestricted": False,
        "enabled_by": None,
        "enabled_at": None,
        "can_change": False,
    }
    _, out = ask(c)
    assert out["status"] == "pending" and "automatic" not in out and WorkflowRun.objects.count() == 0


def test_only_an_administrator_with_a_human_session_can_change_it(ws, world, version, c, admin):
    for user in (world["designer"], world["viewer"]):
        assert human(user).put(auto_url(ws), {"enabled": True}, format="json").status_code == 403
    c_token = token_client(admin, ws, scopes=("view", "edit", "administer"))
    assert c_token.put(auto_url(ws), {"enabled": True}, format="json").status_code == 403  # un jeton ne l'active jamais
    ws.refresh_from_db()
    assert ws.assistant_auto_run is False


def test_when_enabled_a_valid_request_is_launched_at_once_in_the_name_of_the_token_owner(ws, world, version, c, admin):
    r = enable(ws, admin)
    assert r.json()["enabled"] is True and r.json()["enabled_by"] == admin.username
    _, out = ask(c)
    assert out["status"] == "launched" and out["automatic"] is True and out["run_id"]
    run = WorkflowRun.objects.get(pk=out["run_id"])
    assert run.requested_by == world["designer"] and run.environment.kind == "test"
    ev = AuditEvent.objects.get(action="run_request.auto_confirm")
    assert ev.data["automatic"] is True and ev.data["enabled_by"] == admin.username
    assert AuditEvent.objects.filter(action="run_request.auto_setting").exists()


def test_the_production_is_still_refused_when_enabled(ws, world, version, c, admin):
    enable(ws, admin)
    res, out = ask(c, env="prod")
    assert res["isError"] and WorkflowRun.objects.count() == 0 and RunRequest.objects.count() == 0


def test_a_token_owner_who_may_not_run_is_still_refused_when_enabled(ws, world, version, admin):
    enable(ws, admin)
    viewer = world["viewer"]
    res, _ = ask(token_client(viewer, ws))
    assert res["isError"] and WorkflowRun.objects.count() == 0


def test_switching_it_off_goes_back_to_waiting_for_a_person(ws, world, version, c, admin):
    enable(ws, admin)
    off = human(admin).put(auto_url(ws), {"enabled": False}, format="json").json()
    assert off["enabled"] is False and off["enabled_by"] is None
    _, out = ask(c)
    assert out["status"] == "pending" and WorkflowRun.objects.count() == 0
