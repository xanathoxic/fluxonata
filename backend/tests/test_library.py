import pytest

from audit.models import AuditEvent
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import edge, make_action, node, publish

BASE = "/api/v1/workspaces/team-a"
SCHEMA_IN = {
    "type": "object",
    "required": ["n"],
    "properties": {"n": {"type": "integer", "title": "N", "description": "Le nombre à doubler."}},
}
SCHEMA_OUT = {
    "type": "object",
    "required": ["double"],
    "properties": {"double": {"type": "integer", "title": "Double", "description": "Le nombre reçu fois deux."}},
}


def new_action(c, slug="dbl", **over):
    body = {
        "slug": slug,
        "name": "Doubler",
        "description": "Double un nombre entier et renvoie le résultat.",
        "tags": ["math"],
        "version": {
            "source": "def run(i):\n    return {'double': i['n'] * 2}\n",
            "input_schema": SCHEMA_IN,
            "output_schema": SCHEMA_OUT,
        },
    }
    body["version"].update(over)
    return c.post(f"{BASE}/library/actions/", body, format="json")


def test_create_action_with_first_version(world):
    c = client_for(world["designer"])
    r = new_action(c)
    assert r.status_code == 201 and r.json()["latest_version"]["version"] == "1.0.0"
    assert r.json()["latest_version"]["source_digest"].startswith("sha256:")
    assert AuditEvent.objects.filter(action="action.create").exists()
    assert new_action(c).status_code == 400  # duplicate slug
    assert c.get(f"{BASE}/actions/dbl/").status_code == 200


@pytest.mark.parametrize(
    "over,field",
    [
        ({"source": "def run(i:\n    pass"}, "source"),
        ({"input_schema": {"type": "string"}}, "input_schema"),
        ({"output_schema": {"type": "object", "properties": {"a": {"type": "nonsense"}}}}, "output_schema"),
        ({"limits": {"timeout_seconds": 99999}}, "limits"),
        ({"network_policy": "internet"}, "network_policy"),
        ({"runtime": "cobol"}, "runtime"),
        (
            {"source": "KEY='-----BEGIN RSA " + "PRIVATE KEY-----'\ndef run(i): return {}"},
            "source",
        ),
        ({"secrets_required": [{"slot": "Bad Slot", "env": "x"}]}, "secrets_required"),
    ],
)
def test_action_version_validation(world, over, field):
    r = new_action(client_for(world["designer"]), **over)
    assert r.status_code == 400 and field in str(r.json()["error"]["details"])


def test_versions_semver_deprecation_and_permissions(world):
    c = client_for(world["designer"])
    new_action(c)
    body = {
        "source": "def run(i):\n    return {'double': i['n'] + i['n']}\n",
        "input_schema": SCHEMA_IN,
        "output_schema": SCHEMA_OUT,
        "bump": "patch",
    }
    r = c.post(f"{BASE}/actions/dbl/versions/", body, format="json")
    assert r.status_code == 201 and r.json()["version"] == "1.0.1"
    assert c.post(f"{BASE}/actions/dbl/versions/", {**body, "version": "0.9.0"}, format="json").status_code == 400
    assert c.post(f"{BASE}/actions/dbl/versions/", {**body, "version": "1.0.1"}, format="json").status_code == 400
    assert [v["version"] for v in c.get(f"{BASE}/actions/dbl/versions/").json()["results"]] == ["1.0.1", "1.0.0"]
    dep = c.post(f"{BASE}/actions/dbl/deprecate/", {"reason": "remplacée par triple"}, format="json").json()
    assert dep["status"] == "deprecated" and "triple" in dep["deprecation_reason"]
    assert c.post(f"{BASE}/actions/dbl/deprecate/", {"reinstate": True}, format="json").json()["status"] == "active"
    assert client_for(world["viewer"]).post(f"{BASE}/actions/dbl/versions/", body, format="json").status_code == 403
    assert client_for(world["viewer"]).get(f"{BASE}/actions/dbl/versions/").status_code == 200
    assert client_for(world["outsider"]).get(f"{BASE}/actions/dbl/versions/").status_code == 404


def test_published_version_is_immutable_and_rollback_republishes(world):
    c = client_for(world["designer"])
    new_action(c)
    c.post(
        f"{BASE}/actions/dbl/versions/",
        {"source": "def run(i):\n    return {'double': 0}\n", "input_schema": SCHEMA_IN, "output_schema": SCHEMA_OUT},
        format="json",
    )
    rb = c.post(f"{BASE}/actions/dbl/rollback/", {"to_version": "1.0.0"}, format="json")
    assert rb.status_code == 201 and rb.json()["version"] == "1.1.1" and "n'" in rb.json()["source"]
    assert rb.json()["changelog"] == "Retour à la version 1.0.0"
    versions = [v["version"] for v in c.get(f"{BASE}/actions/dbl/versions/").json()["results"]]
    assert versions == ["1.1.1", "1.1.0", "1.0.0"]  # history intact, nothing removed
    assert c.post(f"{BASE}/actions/dbl/rollback/", {"to_version": "9.9.9"}, format="json").status_code == 400


def test_compare_and_breaking_change_detection(world):
    c = client_for(world["designer"])
    new_action(c)
    other_in = {
        "type": "object",
        "required": ["n", "m"],
        "properties": {
            "n": {"type": "integer", "title": "N", "description": "Le premier nombre."},
            "m": {"type": "integer", "title": "M", "description": "Le second nombre."},
        },
    }
    c.post(
        f"{BASE}/actions/dbl/versions/",
        {"source": "def run(i):\n    return {'double': 1}\n", "input_schema": other_in, "output_schema": SCHEMA_OUT},
        format="json",
    )
    r = c.get(f"{BASE}/actions/dbl/compare/?a=1.0.0&b=1.1.0").json()
    assert "input_schema" in r["changed_fields"] and r["breaking"] is True and "-    return" in r["source_diff"]
    assert c.get(f"{BASE}/actions/dbl/compare/?a=1.0.0&b=0.0.1").status_code == 404


def test_reverse_dependencies(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(ws, "uses-echo", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    publish(ws, "caller", [node("s", "subworkflow", workflow="uses-echo", version="1.0.0")], [], user=world["designer"])
    c = client_for(world["designer"])
    deps = c.get(f"{BASE}/actions/echo/dependents/").json()["results"]
    assert deps == [{"workflow": "uses-echo", "workflow_version": "1.0.0", "action_versions": ["1.0.0"]}]
    wdeps = c.get(f"{BASE}/workflows/uses-echo/dependents/").json()["results"]
    assert wdeps[0]["workflow"] == "caller"
    assert client_for(world["outsider"]).get(f"{BASE}/actions/echo/dependents/").status_code == 404


def test_workflow_compare_rollback_and_deprecation(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    publish(ws, "wfc", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])
    publish(
        ws,
        "wfc",
        [node("a", action="echo", inputs={"value": "y"}), node("b", action="echo", inputs={"value": "z"})],
        [edge("a", "b")],
        user=world["designer"],
    )
    c = client_for(world["designer"])
    d = c.get(f"{BASE}/workflows/wfc/compare/?a=1.0.0&b=1.1.0").json()
    assert d["nodes_added"] == ["b"] and d["nodes_changed"] == ["a"] and d["edges_added"] == [["a", "b", None]]
    rb = c.post(f"{BASE}/workflows/wfc/rollback/", {"to_version": "1.0.0"}, format="json")
    assert rb.status_code == 201
    versions = [v["version"] for v in c.get(f"{BASE}/workflows/wfc/versions/").json()["results"]]
    assert versions == ["1.1.1", "1.1.0", "1.0.0"]
    assert (
        c.post(f"{BASE}/workflows/wfc/deprecate/", {"reason": "obsolète"}, format="json").json()["deprecated"] is True
    )


def test_deprecated_action_and_missing_connection_surface_in_validation(world):
    from workflows.validation import validate

    ws = world["ws_a"]
    c = client_for(world["designer"])
    new_action(c, secrets_required=[{"slot": "api", "env": "API_TOKEN"}])
    graph = {
        "inputs": {"type": "object", "properties": {}},
        "outputs": {},
        "edges": [],
        "nodes": [node("a", action="dbl", inputs={"n": 1})],
    }
    assert any(i["code"] == "missing_connection" for i in validate(graph, ws))
    graph["nodes"][0]["connections"] = {"api": "ghost"}
    assert any(i["code"] == "unknown_connection" for i in validate(graph, ws))
    graph["nodes"][0]["connections"] = {"api": "ghost", "other": "x"}
    assert any(i["code"] == "unknown_slot" for i in validate(graph, ws))
    c.post(f"{BASE}/actions/dbl/deprecate/", {"reason": "old"}, format="json")
    warn = [i for i in validate(graph, ws) if i["code"] == "deprecated_action"]
    assert warn and warn[0]["level"] == "warning"
    assert make_user and Role


def test_action_list_does_not_query_per_action(world, django_assert_max_num_queries):
    """The list reads every version once, not once per action (it was 2 queries per action)."""
    c = client_for(world["designer"])
    for i in range(8):
        assert new_action(c, slug=f"dbl-{i}").status_code == 201
    with django_assert_max_num_queries(15):
        r = c.get(f"{BASE}/actions/")
    assert r.status_code == 200 and len(r.json()["results"]) >= 8


def test_action_list_versions_are_serialised_exactly_like_the_detail_serializer(world):
    """AR1.5 : le sérialiseur de version est réutilisé d'une action à l'autre ; le résultat ne doit pas changer d'un octet."""
    import json

    from catalog.models import Action
    from catalog.serializers import ActionVersionSerializer

    c = client_for(world["designer"])
    for i in range(3):
        assert new_action(c, slug=f"same-{i}").status_code == 201
    rows = {a["slug"]: a for a in c.get(f"{BASE}/actions/").json()["results"]}
    for action in Action.objects.filter(slug__startswith="same-"):
        expected = ActionVersionSerializer(action.latest_version()).data
        assert rows[action.slug]["latest_version"] == dict(
            c.get(f"{BASE}/actions/{action.slug}/").json()["latest_version"]
        )
        assert rows[action.slug]["latest_version"] == json.loads(json.dumps(expected))


def test_saved_test_sets_crud_rights_validation_and_audit(world):
    c = client_for(world["designer"])
    new_action(c)
    url = f"{BASE}/actions/dbl/tests/"
    assert c.get(url).json() == {"results": []}

    r = c.post(url, {"name": "Petit nombre", "inputs": {"n": 2}}, format="json")
    assert r.status_code == 201 and r.json()["valid"] is True and r.json()["created_by"] == "dana"
    case = r.json()["id"]
    assert AuditEvent.objects.filter(action="action.testcase_create").exists()

    # le contrat est appliqué, les doublons et les secrets sont refusés
    assert c.post(url, {"name": "Mauvais", "inputs": {"n": "deux"}}, format="json").status_code == 400
    assert c.post(url, {"name": "Petit nombre", "inputs": {"n": 3}}, format="json").status_code == 400
    assert c.post(url, {"name": " ", "inputs": {"n": 3}}, format="json").status_code == 400
    leak = "AKIA" + "A" * 16
    assert c.post(url, {"name": "Clé", "inputs": {"n": 1, "x": leak}}, format="json").status_code == 400

    # modifier (nom seul, puis entrées)
    r = c.patch(f"{url}{case}/", {"inputs": {"n": 10}}, format="json")
    assert r.status_code == 200 and r.json()["inputs"] == {"n": 10} and r.json()["name"] == "Petit nombre"
    assert c.patch(f"{url}{case}/", {"name": "Grand"}, format="json").json()["name"] == "Grand"
    assert c.patch(f"{url}{case}/", {"inputs": {"n": "x"}}, format="json").status_code == 400

    # un lecteur voit les jeux, ne les gère pas ; un autre espace n'y accède pas
    viewer, outsider = client_for(world["viewer"]), client_for(world["outsider"])
    assert [t["name"] for t in viewer.get(url).json()["results"]] == ["Grand"]
    assert viewer.post(url, {"name": "X", "inputs": {"n": 1}}, format="json").status_code == 403
    assert viewer.patch(f"{url}{case}/", {"name": "Y"}, format="json").status_code == 403
    assert viewer.delete(f"{url}{case}/").status_code == 403
    assert outsider.get(url).status_code in (403, 404)
    assert outsider.delete(f"{url}{case}/").status_code in (403, 404)

    # nouvelle version au contrat incompatible : le jeu est conservé mais signalé
    new_version = {
        "source": "def run(i):\n    return {'double': 1}\n",
        "input_schema": {
            **SCHEMA_IN,
            "required": ["n", "m"],
            "properties": {
                **SCHEMA_IN["properties"],
                "m": {"type": "integer", "title": "M", "description": "Le second nombre à additionner."},
            },
        },
        "output_schema": SCHEMA_OUT,
        "bump": "major",
    }
    r = c.post(f"{BASE}/actions/dbl/versions/", new_version, format="json")
    assert r.status_code == 201, r.json()
    assert c.get(url).json()["results"][0]["valid"] is False

    assert c.delete(f"{url}{case}/").status_code == 204
    assert c.get(url).json() == {"results": []}
    assert AuditEvent.objects.filter(action="action.testcase_delete").exists()
