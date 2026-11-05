"""ADR 0025 — une action ou une skill « proposée » (par un assistant externe) n'est jamais exécutée ni publiée avant relecture humaine.

Chaque test ferme UN chemin : exécution (démarrage d'un run, brouillon, compensation, outil d'agent, skill, exécution directe), publication,
visibilité, activation. Le statut est posé par l'ORM : c'est ce que créera `propose_action` / `propose_skill` du MCP de conception.
"""

from types import SimpleNamespace

import pytest

from ai.models import AiSkill
from audit.models import AuditEvent
from catalog.models import Action
from runs import engine, proposed, services
from tenancy.models import Environment
from tenancy.permissions import EDIT, effective_permissions
from tests.conftest import client_for
from tests.wf_helpers import ACTIONS, drain, launch, make_action, node, publish
from workflows.models import Workflow
from workflows.validation import validate

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


def propose(slug="echo"):
    Action.objects.filter(slug=slug).update(status="proposed")


def graph_with(**node_kw):
    return {
        "inputs": {"type": "object"},
        "nodes": [node("a", action="echo", inputs={"value": "x"}, **node_kw)],
        "edges": [],
    }


# --- exécution : tous les chemins sont fermés -------------------------------------------------------------------------------
def test_a_published_workflow_using_a_proposed_action_does_not_start(ws, world):
    v = publish(ws, "w", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    propose()
    with pytest.raises(proposed.ProposedResource):
        launch(ws, v, world["designer"])


def test_a_draft_test_run_with_a_proposed_action_does_not_start(ws, world):
    v = publish(ws, "w", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    g = {**v.graph, "nodes": [{**v.graph["nodes"][0]}]}
    propose()
    with pytest.raises(proposed.ProposedResource):
        engine.start_run(
            workspace=ws,
            environment=Environment.objects.get(workspace=ws, kind="test"),
            workflow_version=v,
            user=world["designer"],
            inputs={},
            graph_override=g,
        )


@pytest.mark.parametrize(
    "graph",
    [
        {"nodes": [node("a", action="echo", version="1.0.0", compensation={"action": "echo", "version": "1.0.0"})]},
        {"nodes": [node("g", "agent", tools=["echo"], goal="x")]},
    ],
    ids=["compensation", "outil d'agent"],
)
def test_every_action_reference_of_a_graph_is_checked(ws, graph):
    other = Action.objects.create(workspace=ws, slug="other", name="other")  # une autre action active ne protège rien
    assert other.status == "active"
    proposed.refuse_proposed_in_graph(ws, graph)  # tout est actif : passe
    propose()
    with pytest.raises(proposed.ProposedResource):
        proposed.refuse_proposed_in_graph(ws, graph)


def test_enqueue_is_the_last_barrier_for_any_direct_execution(ws, world):
    av = Action.objects.get(slug="echo").versions.first()
    kw = dict(
        workspace=ws,
        environment=Environment.objects.get(workspace=ws, kind="test"),
        action_version=av,
        user=world["designer"],
        inputs={"value": "x"},
    )
    assert services.enqueue(**kw).pk  # active : accepté
    propose()
    kw["action_version"] = Action.objects.get(
        slug="echo"
    ).versions.first()  # instance relue : le statut est celui de la base
    with pytest.raises(proposed.ProposedResource):
        services.enqueue(**kw)
    with pytest.raises(proposed.ProposedResource):
        services.create_execution(**kw)


def test_a_proposed_skill_does_not_start(ws, world):
    from ai.models import AiSkillVersion

    s = AiSkill.objects.create(workspace=ws, slug="sk", name="sk")
    AiSkillVersion.objects.create(skill=s, version="1.0.0", instructions="x", input_schema={}, output_schema={})
    g = {"nodes": [node("s", "ai", skill="sk", skill_version="1.0.0")]}
    proposed.refuse_proposed_in_graph(ws, g)
    AiSkill.objects.filter(slug="sk").update(status="proposed")
    with pytest.raises(proposed.ProposedResource):
        proposed.refuse_proposed_in_graph(ws, g)


# --- publication ------------------------------------------------------------------------------------------------------------
def test_validation_flags_the_proposed_action_and_publication_is_refused(ws, world):
    propose()
    issues = validate(graph_with(version="1.0.0"), ws, "w")
    assert [i["code"] for i in issues if i["level"] == "error"] == ["proposed_action"]
    wf = Workflow.objects.create(workspace=ws, slug="w", name="w", draft_graph=graph_with(version="1.0.0"))
    r = client_for(world["designer"]).post(f"{BASE}/workflows/{wf.slug}/publish/", {"bump": "minor"}, format="json")
    assert r.status_code == 400 and "proposed_action" in str(r.json())
    assert not wf.versions.exists()
    Action.objects.filter(slug="echo").update(status="active")  # une fois relue, la même publication passe
    assert (
        client_for(world["designer"]).post(f"{BASE}/workflows/w/publish/", {"bump": "minor"}, format="json").status_code
        == 201
    )


# --- visibilité -------------------------------------------------------------------------------------------------------------
def test_proposals_are_listed_only_on_request_and_only_for_people_who_can_edit(ws, world):
    propose()
    d, v = client_for(world["designer"]), client_for(world["viewer"])
    slugs = lambda c, q="": {a["slug"] for a in c.get(f"{BASE}/actions/{q}").json()["results"]}  # noqa: E731
    assert "echo" not in slugs(d) and "echo" in slugs(d, "?status=proposed")
    assert slugs(v, "?status=proposed").isdisjoint({"echo"}) and "echo" not in slugs(v)  # le lecteur ne les voit jamais
    assert d.get(f"{BASE}/actions/echo/").status_code == 200 and v.get(f"{BASE}/actions/echo/").status_code == 404


def test_proposed_skills_follow_the_same_visibility(ws, world):
    from ai.models import AiSkillVersion

    s = AiSkill.objects.create(workspace=ws, slug="sk", name="sk", status="proposed")
    AiSkillVersion.objects.create(skill=s, version="1.0.0", instructions="x", input_schema={}, output_schema={})
    d, v = client_for(world["designer"]), client_for(world["viewer"])
    names = lambda c, q="": {a["slug"] for a in c.get(f"{BASE}/ai/skills/{q}").json()["results"]}  # noqa: E731
    assert names(d) == set() and names(d, "?status=proposed") == {"sk"} and names(v, "?status=proposed") == set()
    assert d.get(f"{BASE}/ai/skills/sk/").status_code == 200 and v.get(f"{BASE}/ai/skills/sk/").status_code == 404


# --- activation : un acte humain ----------------------------------------------------------------------------------------------
def test_activation_by_a_person_makes_the_action_usable(ws, world):
    v = publish(ws, "w", [node("e", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    propose()
    assert client_for(world["viewer"]).post(f"{BASE}/actions/echo/activate/").status_code == 403
    assert Action.objects.get(slug="echo").status == "proposed"
    assert client_for(world["designer"]).post(f"{BASE}/actions/echo/activate/").status_code == 200
    assert Action.objects.get(slug="echo").status == "active"
    assert AuditEvent.objects.filter(action="action.activate").exists()
    assert drain(launch(ws, v, world["designer"])).status == "succeeded"  # puis elle s'exécute normalement
    assert client_for(world["designer"]).post(f"{BASE}/actions/echo/activate/").status_code == 400  # déjà active


def test_a_token_can_never_activate_nor_reinstate_into_active(ws, world):
    propose()
    user = world["designer"]
    user._token = SimpleNamespace(scopes=["view", "edit", "publish"], workspace_id=ws.id, environments=[])
    c = client_for(user)  # même un jeton portant « publish » ne relit pas à la place d'une personne
    assert c.post(f"{BASE}/actions/echo/activate/").status_code == 403
    assert Action.objects.get(slug="echo").status == "proposed"
    # « Réactiver » n'est pas un autre chemin vers « active »
    del user._token
    r = client_for(user).post(f"{BASE}/actions/echo/deprecate/", {"reinstate": True}, format="json")
    assert r.status_code == 400 and Action.objects.get(slug="echo").status == "proposed"


def test_skill_activation_follows_the_same_rules(ws, world):
    from ai.models import AiSkillVersion

    s = AiSkill.objects.create(workspace=ws, slug="sk", name="sk", status="proposed")
    AiSkillVersion.objects.create(skill=s, version="1.0.0", instructions="x", input_schema={}, output_schema={})
    user = world["designer"]
    assert client_for(world["viewer"]).post(f"{BASE}/ai/skills/sk/activate/").status_code == 403
    assert (
        client_for(user).post(f"{BASE}/ai/skills/sk/deprecate/", {"reinstate": True}, format="json").status_code == 400
    )
    user._token = SimpleNamespace(scopes=["view", "edit", "publish"], workspace_id=ws.id, environments=[])
    assert client_for(user).post(f"{BASE}/ai/skills/sk/activate/").status_code == 403
    del user._token
    assert client_for(user).post(f"{BASE}/ai/skills/sk/activate/").status_code == 200
    assert (
        AiSkill.objects.get(slug="sk").status == "active"
        and AuditEvent.objects.filter(action="ai_skill.activate").exists()
    )


def test_the_edit_scope_alone_never_reaches_execution_or_publication():
    """Le profil de conception (view + edit) : l'absence de run/publish est portée par le jeton, pas par la documentation."""
    from tenancy.models import Role
    from tenancy.permissions import ROLE_PERMISSIONS

    token = SimpleNamespace(scopes=["view", "edit"])
    m = SimpleNamespace(role=Role.DESIGNER)
    perms = effective_permissions(SimpleNamespace(_token=token), m)
    assert EDIT in perms and perms.isdisjoint({"run", "publish", "approve", "administer", "manage_connections"})
    assert "run" in ROLE_PERMISSIONS[Role.DESIGNER]  # le rôle seul le permettrait : c'est bien le jeton qui borne
