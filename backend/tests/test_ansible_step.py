"""L'étape « Playbook Ansible » d'un workflow : le playbook est écrit dans le workflow, appelle des rôles de la bibliothèque (versions figées à la publication) et
suit le chemin d'une action Ansible (runner à étiquettes, inventaire, connexions, rôles remis au runner)."""

import pytest

from catalog import ansible_inline as inline
from catalog.models import Action, ActionVersion, AnsibleRole, AnsibleRoleVersion, Inventory
from runs import engine
from runs.models import Execution
from tenancy.models import Environment
from tests.conftest import client_for
from tests.wf_helpers import launch, node, publish, schema
from vault.models import Connection, Secret
from workflows.models import Workflow
from workflows.validation import pin_graph, validate

BASE = "/api/v1/workspaces/team-a"
PLAYBOOK = "- name: Page web\n  hosts: all\n  gather_facts: false\n  roles:\n    - role: web\n"
INVENTORY = {"all": {"hosts": {"web1": {"ansible_host": "10.0.0.5"}}}}


def step(**over):
    fields = {
        "name": "Déployer",
        "playbook": PLAYBOOK,
        "roles": [{"role": "web", "version": "latest"}],
        "required_tags": ["ansible-lab"],
        "connections": {"ssh": "lab-ssh"},
        "inputs": {"inventory_ref": "lab", "known_hosts": "web1 ssh-ed25519 AAAA", "check": True},
    }
    return node("deploy", "ansible", **{**fields, **over})


@pytest.fixture
def space(world):
    ws = world["ws_a"]
    role = AnsibleRole.objects.create(workspace=ws, name="web")
    AnsibleRoleVersion.objects.create(
        role=role, version="1.0.0", files={"tasks/main.yml": "- ansible.builtin.debug: {msg: a}\n"}
    )
    AnsibleRoleVersion.objects.create(
        role=role, version="1.1.0", files={"tasks/main.yml": "- ansible.builtin.debug: {msg: b}\n"}
    )
    inv = Inventory.objects.create(workspace=ws, slug="lab", name="Labo")
    inv.hosts.create(name="web1", variables={"ansible_host": "10.0.0.5"}, enabled=True)
    secret = Secret.objects.create(workspace=ws, name="k")
    secret.set_value("cle")
    secret.save()
    Connection.objects.create(workspace=ws, name="lab-ssh", kind="generic", secret=secret)
    return world


def graph(*nodes):
    return {"inputs": schema(), "nodes": list(nodes), "edges": [], "outputs": {}}


def codes(world, g, level="error"):
    return {i["code"] for i in validate(g, world["ws_a"], "wf") if i["level"] == level}


def reader(field):
    return node(
        "suite",
        "script",
        language="python",
        script="x = 1\n",
        bindings={"v": f"{{{{ steps.deploy.output.{field} }}}}"},
        outputs=[{"name": "x", "type": "integer"}],
    )


def test_a_valid_step_has_no_error_and_its_outputs_are_known(space):
    ok = graph(step(), reader("ok"))
    ok["edges"] = [{"from": "deploy", "to": "suite"}]
    assert [i for i in validate(ok, space["ws_a"], "wf") if i["level"] == "error"] == []
    typo = graph(step(), reader("inconnu"))
    typo["edges"] = [{"from": "deploy", "to": "suite"}]
    assert any("inconnu" in i["message"] for i in validate(typo, space["ws_a"], "wf") if i["level"] == "error")


def test_the_playbook_and_the_runner_tags_are_checked_like_an_action_version(space):
    assert "empty_playbook" in codes(space, graph(step(playbook="  ")))
    assert "bad_playbook" in codes(space, graph(step(playbook="pas du yaml : [")))
    assert "missing_runner_tags" in codes(space, graph(step(required_tags=[])))


def test_roles_must_exist_and_their_dependencies_be_declared(space):
    assert "bad_role" in codes(space, graph(step(roles=[{"role": "inconnu", "version": "latest"}])))
    assert "bad_role" in codes(space, graph(step(roles=[{"role": "web", "version": "9.9.9"}])))
    AnsibleRoleVersion.objects.create(
        role=AnsibleRole.objects.get(name="web"),
        version="1.2.0",
        files={
            "tasks/main.yml": "- ansible.builtin.debug: {msg: c}\n",
            "meta/main.yml": "dependencies:\n  - role: base\n",
        },
    )
    assert "bad_role" in codes(
        space, graph(step())
    )  # « latest » est maintenant 1.2.0, qui dépend de base : à déclarer aussi


def test_the_inventory_the_connections_and_the_parameters_are_checked(space):
    assert "bad_inventory" in codes(space, graph(step(inputs={"known_hosts": "x"})))
    assert "bad_inventory" in codes(
        space, graph(step(inputs={"known_hosts": "x", "inventory_ref": "lab", "inventory": {"all": {}}}))
    )
    assert "missing_param" in codes(space, graph(step(inputs={"inventory_ref": "lab"})))  # known_hosts est obligatoire
    assert "unknown_param" in codes(space, graph(step(inputs={"inventory_ref": "lab", "known_hosts": "x", "oups": 1})))
    assert "unknown_connection" in codes(space, graph(step(connections={"ssh": "absente"})))
    assert "unknown_slot" in codes(space, graph(step(connections={"autre": "lab-ssh"})))
    assert "missing_connection" in codes(
        space, graph(step(connections={})), "warning"
    )  # sans connexion SSH : avertissement, pas erreur


def test_an_error_edge_is_allowed_after_the_step(space):
    g = graph(step(), node("secours", "throw", message="échec"))
    g["edges"] = [{"from": "deploy", "to": "secours", "label": "error"}]
    assert "bad_branches" not in codes(space, g)


def test_publication_freezes_the_roles_to_exact_versions(space):
    pinned = pin_graph(graph(step()), space["ws_a"])
    assert pinned["nodes"][0]["roles"] == [{"role": "web", "version": "1.1.0"}]
    exact = pin_graph(graph(step(roles=[{"role": "web", "version": "1.0.0"}])), space["ws_a"])
    assert exact["nodes"][0]["roles"] == [{"role": "web", "version": "1.0.0"}]  # une version choisie ne bouge pas


def test_a_run_creates_a_hidden_action_version_and_queues_the_execution_like_an_action(space):
    ws, user = space["ws_a"], space["designer"]
    version = publish(ws, "deploiement", [step()], [], user=user)
    run = launch(ws, version, user)
    engine.advance(run.pk)
    ex = Execution.objects.get(step_run__run=run)
    av = ex.action_version
    assert av.action.slug == "__playbook__" and av.runtime == "ansible" and av.network_policy == "targets"
    assert (
        av.required_tags == ["ansible-lab"]
        and av.source == PLAYBOOK
        and av.side_effects == "external"
        and av.idempotent is False
    )
    assert av.ansible_roles == [{"role": "web", "version": "1.1.0"}]  # figé à la publication
    assert av.secrets_required == [{"slot": "ssh", "env": "FLX_SSH_KEY_B64"}]
    assert ex.inputs == {
        "inventory_ref": "lab",
        "known_hosts": "web1 ssh-ed25519 AAAA",
        "check": True,
    } and ex.connections == {"ssh": "lab-ssh"}
    assert ex.status == "queued" and ex.step_run.node_type == "ansible"


def test_the_same_playbook_reuses_its_version_and_a_change_makes_another(space):
    ws, user = space["ws_a"], space["designer"]
    a = inline.inline_version(ws, user, step())
    assert inline.inline_version(ws, user, step()).pk == a.pk
    assert inline.inline_version(ws, user, step(playbook=PLAYBOOK + "      tags: [x]\n")).pk != a.pk
    assert inline.inline_version(ws, user, step(auth="password")).secrets_required == [
        {"slot": "ssh", "env": "FLX_SSH_PASSWORD"}
    ]
    assert ActionVersion.objects.filter(action__slug="__playbook__").count() == 3
    assert not Action.objects.filter(workspace=ws, slug="__playbook__").exclude(slug__startswith="__").exists()


def test_empty_parameters_are_not_sent_and_an_invalid_one_fails_the_step_clearly(space):
    ws, user = space["ws_a"], space["designer"]
    run = launch(
        ws,
        publish(
            ws,
            "vide",
            [step(inputs={"inventory_ref": "lab", "known_hosts": "k", "limit": "", "extra_vars": ""})],
            [],
            user=user,
        ),
        user,
    )
    engine.advance(run.pk)
    assert Execution.objects.get(step_run__run=run).inputs == {"inventory_ref": "lab", "known_hosts": "k"}
    bad = publish(
        ws,
        "mauvais",
        [step(inputs={"inventory_ref": "lab", "known_hosts": "k", "forks": "{{ inputs.n }}"})],
        [],
        inputs=schema(["n"], n={"type": "integer"}),
        user=user,
    )
    run2 = launch(ws, bad, user, {"n": 99})
    engine.advance(run2.pk)
    s = run2.steps.get(node_id="deploy")
    assert s.status == "failed" and s.error["code"] == "INPUT_INVALID" and "forks" in s.error["message"]


def test_a_playbook_that_became_invalid_fails_the_step_without_crashing_the_engine(space):
    ws, user = space["ws_a"], space["designer"]
    version = publish(ws, "casse", [step()], [], user=user)
    AnsibleRoleVersion.objects.filter(
        role__name="web"
    ).delete()  # le rôle figé a disparu (ne peut pas arriver via l'API : il est protégé)
    run = launch(ws, version, user)
    engine.advance(run.pk)
    s = run.steps.get(node_id="deploy")
    assert s.status == "failed" and s.error["code"] == "ANSIBLE_CONFIG"


def test_the_step_schema_is_served_from_one_source(space):
    c = client_for(space["designer"])
    r = c.get(f"{BASE}/ansible-step/")
    assert (
        r.status_code == 200
        and r.json()["input_schema"] == inline.INPUT_SCHEMA
        and "stats" in r.json()["output_schema"]["properties"]
    )
    assert client_for(space["outsider"]).get(f"{BASE}/ansible-step/").status_code == 404


def test_the_test_endpoint_queues_the_step_on_the_real_path_and_checks_what_it_receives(space):
    ws = space["ws_a"]
    Workflow.objects.create(workspace=ws, slug="wf", name="WF", owner=space["designer"], draft_graph={"nodes": []})
    c = client_for(space["designer"])
    url = f"{BASE}/workflows/wf/test-ansible/"
    ok = c.post(
        url,
        {
            "node": step(),
            "inputs": {"inventory_ref": "lab", "known_hosts": "k", "check": True},
            "connections": {"ssh": "lab-ssh"},
        },
        format="json",
    )
    assert ok.status_code == 202, ok.content
    ex = Execution.objects.get(pk=ok.json()["execution_id"])
    assert (
        ex.action_version.action.slug == "__playbook__"
        and ex.connections == {"ssh": "lab-ssh"}
        and ex.step_run is None
        and ex.status == "queued"
    )
    assert ex.environment == Environment.objects.get(workspace=ws, kind="test")
    bad = c.post(
        url, {"node": step(required_tags=[]), "inputs": {"inventory_ref": "lab", "known_hosts": "k"}}, format="json"
    )
    assert bad.status_code == 400 and "missing_runner_tags" in str(bad.json())
    both = c.post(url, {"node": step(), "inputs": {"known_hosts": "k"}}, format="json")
    assert both.status_code == 400 and "bad_inventory" in str(both.json())
    typo = c.post(
        url, {"node": step(), "inputs": {"inventory_ref": "lab", "known_hosts": "k", "forks": 99}}, format="json"
    )
    assert typo.status_code == 400
    assert Execution.objects.filter(step_run__isnull=True).count() == 1


def test_the_test_endpoint_needs_the_right_to_edit_and_run_and_an_existing_workflow(space):
    ws = space["ws_a"]
    Workflow.objects.create(workspace=ws, slug="wf", name="WF", owner=space["designer"], draft_graph={"nodes": []})
    body = {"node": step(), "inputs": {"inventory_ref": "lab", "known_hosts": "k"}}
    url = f"{BASE}/workflows/wf/test-ansible/"
    assert client_for(space["viewer"]).post(url, body, format="json").status_code == 403
    assert client_for(space["outsider"]).post(url, body, format="json").status_code == 404
    assert (
        client_for(space["designer"]).post(f"{BASE}/workflows/absent/test-ansible/", body, format="json").status_code
        == 404
    )


def test_a_role_pinned_by_a_published_workflow_cannot_be_deleted_and_hidden_versions_do_not_count(space):
    ws, user = space["ws_a"], space["designer"]
    c = client_for(user)
    inline.inline_version(ws, user, step())  # la version cachée créée à l'exécution ne protège rien à elle seule
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["used_by"] == 0
    publish(ws, "deploiement", [step()], [], user=user)
    assert c.get(f"{BASE}/ansible-roles/").json()[0]["used_by"] == 1
    r = c.delete(f"{BASE}/ansible-roles/web/")
    assert r.status_code == 400 and "workflow" in str(r.json())


def test_the_guide_example_of_the_step_is_valid_once_its_placeholders_are_real(space):
    from workflows.mcp_design_guide import OTHER_NODES

    ws = space["ws_a"]
    example = OTHER_NODES["ansible (playbook écrit dans le workflow)"]
    real = {
        **example,
        "playbook": example["playbook"].replace("<nom d'un rôle de list_ansible_roles>", "web"),
        "roles": [{"role": "web", "version": "latest"}],
        "required_tags": ["ansible-lab"],
        "connections": {"ssh": "lab-ssh"},
        "inputs": {**example["inputs"], "inventory_ref": "lab"},
    }
    g = {"inputs": schema(known_hosts={"type": "string"}), "nodes": [real], "edges": [], "outputs": {}}
    assert [i for i in validate(g, ws, "x") if i["level"] == "error"] == []
    assert "ÉTAPE DE WORKFLOW" in __import__("workflows.mcp_design_guide", fromlist=["guide"]).guide("ansible")["guide"]


def test_a_playbook_written_by_an_assistant_must_be_reviewed_before_publication_and_it_cannot_lift_the_mark(space):
    from tests.test_mcp_design_write import call

    ws, user = space["ws_a"], space["designer"]
    c = client_for(user)
    graph = {"inputs": schema(), "nodes": [step()], "edges": [], "outputs": {}}
    _, made = call(c, "create_workflow", {"slug": "par-assistant", "name": "Par assistant", "graph": graph})
    wf = Workflow.objects.get(workspace=ws, slug="par-assistant")
    assert (
        wf.draft_graph["nodes"][0]["ai_review"] is True
    )  # marqué à la création, même si l'assistant ne l'a pas demandé
    assert "ai_review_required" in {i["code"] for i in made["issues"] if i["level"] == "error"}
    # il ne peut pas lever la marque en la renvoyant fausse
    lifted = {**graph, "nodes": [{**step(), "ai_review": False}]}
    _, again = call(c, "update_workflow", {"slug": "par-assistant", "revision": wf.draft_revision, "graph": lifted})
    wf.refresh_from_db()
    assert wf.draft_graph["nodes"][0]["ai_review"] is True and again is not None
    # une personne relit (marque levée), puis l'assistant renvoie l'étape telle quelle : elle reste relue ; modifiée, elle redevient à relire
    wf.draft_graph["nodes"][0]["ai_review"] = False
    wf.save()
    same = {**graph, "nodes": [{**step(), "ai_review": True}]}  # même contenu : l'état stocké (relu) l'emporte
    call(c, "update_workflow", {"slug": "par-assistant", "revision": wf.draft_revision, "graph": same})
    wf.refresh_from_db()
    assert wf.draft_graph["nodes"][0]["ai_review"] is False
    changed = {**graph, "nodes": [step(playbook=PLAYBOOK + "      tags: [x]\n")]}
    call(c, "update_workflow", {"slug": "par-assistant", "revision": wf.draft_revision, "graph": changed})
    wf.refresh_from_db()
    assert wf.draft_graph["nodes"][0]["ai_review"] is True
