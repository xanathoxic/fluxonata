import pytest

from ai.models import AiPolicy, AiProvider, AiSkillVersion
from ai.providers import FakeProvider
from audit.models import AuditEvent
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, make_action, node, publish
from workflows.validation import validate

BASE = "/api/v1/workspaces/team-a"
IN = {
    "type": "object",
    "required": ["texte"],
    "properties": {"texte": {"type": "string"}, "langue": {"type": "string"}},
}
OUT = {"type": "object", "required": ["label"], "properties": {"label": {"type": "string"}}}


def body(slug="qualifier", **over):
    v = {
        "instructions": "Qualifie ce texte ({{ inputs.langue }}) : {{ inputs.texte }}",
        "input_schema": IN,
        "output_schema": OUT,
        "default_provider": "fake",
    }
    v.update(over.pop("version", {}))
    return {
        "slug": slug,
        "name": "Qualifier un texte",
        "description": "Classe un texte",
        "folder": "Texte/Analyse",
        "version": v,
        **over,
    }


@pytest.fixture
def ai(world):
    ws = world["ws_a"]
    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict"})
    AiProvider.objects.create(workspace=ws, name="fake", kind="fake")
    return ws


def create(world, **kw):
    return client_for(world["designer"]).post(f"{BASE}/ai/skills/", body(**kw), format="json")


def test_create_skill_with_first_version_folder_and_audit(ai, world):
    r = create(world)
    assert r.status_code == 201, r.content
    d = r.json()
    assert (
        d["folder"] == "Texte/Analyse"
        and d["latest_version"]["version"] == "1.0.0"
        and d["latest_version"]["digest"].startswith("sha256:")
    )
    assert AuditEvent.objects.filter(action="ai_skill.create").exists()
    assert create(world).status_code == 400  # duplicate slug
    assert [s["slug"] for s in client_for(world["viewer"]).get(f"{BASE}/ai/skills/").json()["results"]] == ["qualifier"]


@pytest.mark.parametrize(
    "over,field",
    [
        ({"instructions": "  "}, "instructions"),
        ({"instructions": "Bonjour {{ inputs.inconnue }}"}, "instructions"),
        ({"instructions": "Bonjour {{ steps.a.output.x }}"}, "instructions"),
        ({"instructions": "cle -----BEGIN RSA " + "PRIVATE KEY----- {{ inputs.texte }}"}, "instructions"),
        ({"output_schema": {"type": "object", "properties": {}}}, "output_schema"),
        ({"output_schema": {"type": "object", "properties": {"Bad Name": {"type": "string"}}}}, "output_schema"),
        ({"output_schema": {"type": "object", "properties": {"x": {"type": "object"}}}}, "output_schema"),
        ({"input_schema": {"type": "string"}}, "input_schema"),
        (
            {"input_schema": {"type": "object", "required": ["z"], "properties": {"a": {"type": "string"}}}},
            "input_schema",
        ),
    ],
)
def test_version_validation(ai, world, over, field):
    r = create(world, version=over)
    assert r.status_code == 400 and field in str(r.json()["error"]["details"]), r.content


def test_unknown_default_provider_is_refused(ai, world):
    assert create(world, version={"default_provider": "nope"}).status_code == 400


def test_permissions_and_isolation(ai, world):
    assert client_for(world["viewer"]).post(f"{BASE}/ai/skills/", body(), format="json").status_code == 403
    create(world)
    assert client_for(world["outsider"]).get(f"{BASE}/ai/skills/").status_code == 404
    assert client_for(world["outsider"]).get(f"{BASE}/ai/skills/qualifier/").status_code == 404
    assert (
        client_for(world["viewer"]).patch(f"{BASE}/ai/skills/qualifier/", {"folder": "x"}, format="json").status_code
        == 403
    )


def test_versions_are_immutable_semver_and_listed_newest_first(ai, world):
    create(world)
    c = client_for(world["designer"])
    r = c.post(
        f"{BASE}/ai/skills/qualifier/versions/",
        {"instructions": "Nouveau {{ inputs.texte }}", "output_schema": OUT, "input_schema": IN, "bump": "patch"},
        format="json",
    )
    assert r.status_code == 201 and r.json()["version"] == "1.0.1"
    assert (
        c.post(
            f"{BASE}/ai/skills/qualifier/versions/",
            {"instructions": "x", "output_schema": OUT, "version": "0.9.0"},
            format="json",
        ).status_code
        == 400
    )
    assert [v["version"] for v in c.get(f"{BASE}/ai/skills/qualifier/").json()["versions"]] == ["1.0.1", "1.0.0"]
    v = AiSkillVersion.objects.get(version="1.0.0")
    v.instructions = "tampered"
    with pytest.raises(PermissionError):
        v.save()
    with pytest.raises(PermissionError):
        v.delete()


def test_move_rename_and_deprecate(ai, world):
    create(world)
    c = client_for(world["designer"])
    r = c.patch(f"{BASE}/ai/skills/qualifier/", {"folder": " Texte / Autre ", "name": "Qualif"}, format="json").json()
    assert r["folder"] == "Texte/Autre" and r["name"] == "Qualif"
    assert c.patch(f"{BASE}/ai/skills/qualifier/", {"folder": "a" * 61}, format="json").status_code == 400
    assert c.patch(f"{BASE}/ai/skills/qualifier/", {"name": " "}, format="json").status_code == 400
    assert (
        c.post(f"{BASE}/ai/skills/qualifier/deprecate/", {"reason": "remplacé"}, format="json").json()["status"]
        == "deprecated"
    )
    assert (
        c.post(f"{BASE}/ai/skills/qualifier/deprecate/", {"reinstate": True}, format="json").json()["status"]
        == "active"
    )


def ai_node(**over):
    return node(
        "i", "ai", skill="qualifier", inputs={"texte": "{{ steps.a.output.value }}", "langue": "français"}, **over
    )


def test_workflow_validation_with_a_skill(ai, world):
    create(world)
    ws = ai
    make_action(ws, "echo")
    graph = {
        "inputs": {"type": "object", "properties": {}},
        "outputs": {},
        "edges": [{"from": "a", "to": "i"}],
        "nodes": [node("a", action="echo", inputs={"value": "x"}), ai_node()],
    }
    assert [i for i in validate(graph, ws) if i["level"] == "error"] == []
    bad = {**graph, "nodes": [graph["nodes"][0], node("i", "ai", skill="inconnu", inputs={})]}
    assert any(i["code"] == "unknown_skill" for i in validate(bad, ws))
    missing = {**graph, "nodes": [graph["nodes"][0], node("i", "ai", skill="qualifier", inputs={"langue": "fr"})]}
    assert any(i["code"] == "missing_param" for i in validate(missing, ws))
    extra = {**graph, "nodes": [graph["nodes"][0], node("i", "ai", skill="qualifier", inputs={"texte": "t", "x": "y"})]}
    assert any(i["code"] == "unknown_param" for i in validate(extra, ws))
    noprov = {**graph, "nodes": [graph["nodes"][0], ai_node(provider="absent")]}
    assert any(i["code"] == "unknown_provider" for i in validate(noprov, ws))
    client_for(world["designer"]).post(f"{BASE}/ai/skills/qualifier/deprecate/", {"reason": "old"}, format="json")
    assert any(i["code"] == "deprecated_skill" and i["level"] == "warning" for i in validate(graph, ws))


def test_free_form_ai_step_still_works_and_needs_a_task_or_a_skill(ai, world):
    ws = ai
    graph = {
        "inputs": {"type": "object", "properties": {}},
        "outputs": {},
        "edges": [],
        "nodes": [node("i", "ai", provider="fake", prompt="", output_schema=OUT)],
    }
    assert any(i["code"] == "missing_prompt" for i in validate(graph, ws))


def test_publish_pins_the_skill_version_and_the_run_uses_it(ai, world, monkeypatch):
    create(world)
    ws = ai
    make_action(ws, "echo")
    v = publish(
        ws,
        "avec-skill",
        [node("a", action="echo", inputs={"value": "bonjour le monde"}), ai_node()],
        [{"from": "a", "to": "i"}],
        outputs={"l": "{{ steps.i.output.label }}"},
        user=world["designer"],
    )
    n = next(x for x in v.graph["nodes"] if x["id"] == "i")
    assert n["skill_version"] == "1.0.0" and n["provider"] == "fake"
    # a newer skill version must NOT change what this published workflow does
    client_for(world["designer"]).post(
        f"{BASE}/ai/skills/qualifier/versions/",
        {"instructions": "AUTRE {{ inputs.texte }}", "output_schema": OUT, "input_schema": IN},
        format="json",
    )
    from ai.runner import AiRunner

    provider = FakeProvider(script='{"label": "neutre"}')
    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, p=None: setattr(self, "execution", execution) or setattr(self, "provider", provider),
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "succeeded" and run.outputs == {"l": "neutre"}
    prompt = provider.calls[0][1]
    assert "bonjour le monde" in prompt and "français" in prompt and "AUTRE" not in prompt
    assert AuditEvent.objects.filter(action__startswith="ai_skill.").count() == 2


def test_dependents_and_workflow_dependencies(ai, world):
    create(world)
    ws = ai
    make_action(ws, "echo")
    publish(
        ws,
        "dep",
        [node("a", action="echo", inputs={"value": "x"}), ai_node()],
        [{"from": "a", "to": "i"}],
        user=world["designer"],
    )
    c = client_for(world["designer"])
    assert c.get(f"{BASE}/ai/skills/qualifier/dependents/").json()["results"] == [
        {"workflow": "dep", "workflow_version": "1.0.0", "skill_versions": ["1.0.0"]}
    ]
    deps = c.get(f"{BASE}/workflows/dep/dependencies/").json()
    assert (
        deps["skills"][0]["slug"] == "qualifier"
        and deps["skills"][0]["pinned"] == "1.0.0"
        and "fake" in deps["ai_providers"]
    )
    assert client_for(world["outsider"]).get(f"{BASE}/ai/skills/qualifier/dependents/").status_code == 404


def test_skill_and_action_folder_endpoints(ai, world):
    make_action(ai, "echo")
    c = client_for(world["designer"])
    assert (
        c.patch(f"{BASE}/actions/echo/meta/", {"folder": "Infra / Réseau"}, format="json").json()["folder"]
        == "Infra/Réseau"
    )
    assert [a["folder"] for a in c.get(f"{BASE}/actions/").json()["results"] if a["slug"] == "echo"] == ["Infra/Réseau"]
    assert c.patch(f"{BASE}/actions/echo/meta/", {"folder": "x" * 61}, format="json").status_code == 400
    assert (
        client_for(world["viewer"]).patch(f"{BASE}/actions/echo/meta/", {"folder": "z"}, format="json").status_code
        == 403
    )
    assert (
        client_for(world["outsider"]).patch(f"{BASE}/actions/echo/meta/", {"folder": "z"}, format="json").status_code
        == 404
    )
    assert AuditEvent.objects.filter(action="action.update").exists()
    new = {
        "slug": "dbl",
        "name": "Doubler",
        "folder": "Maths",
        "version": {
            "source": "def run(i):\n    return {'double': 1}\n",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"double": {"type": "integer"}}},
        },
    }
    assert c.post(f"{BASE}/library/actions/", new, format="json").json()["folder"] == "Maths"
