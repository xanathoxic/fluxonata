"""Fidélité du schéma OpenAPI : la réponse RÉELLE de chaque endpoint décrit ci-dessous est validée contre le schéma généré.

Les clés non documentées sont refusées (`additionalProperties: false` ajouté aux objets qui déclarent des propriétés) : un champ
ajouté à une réponse sans mise à jour du schéma fait échouer le test. Ne teste que les 16 opérations dont la réponse était décrite par le
serializer de la requête ; aucun comportement de l'API n'est modifié."""

import copy

import pytest
from drf_spectacular.generators import SchemaGenerator
from jsonschema import Draft7Validator

from ai.models import AiPolicy, AiProvider
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.form_helpers import BASE, definition, field
from tests.test_ai_skills import IN, OUT, body
from tests.test_form_controls import ADDRESS
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema

W = "/api/v1/workspaces/{workspace}"
pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def doc():
    return SchemaGenerator().get_schema(request=None, public=True)


def _inline(node_, schemas, seen=()):
    """Remplace les $ref par leur contenu, convertit `nullable` et interdit les clés non déclarées."""
    if isinstance(node_, list):
        return [_inline(x, schemas, seen) for x in node_]
    if not isinstance(node_, dict):
        return node_
    if "$ref" in node_:
        name = node_["$ref"].rsplit("/", 1)[-1]
        assert name not in seen, f"référence circulaire : {name}"
        return _inline(copy.deepcopy(schemas[name]), schemas, (*seen, name))
    out = {k: _inline(v, schemas, seen) for k, v in node_.items()}
    if out.get("nullable") and "type" in out:
        out["type"] = [out["type"], "null"]
    if out.get("nullable") and "allOf" in out:  # $ref nullable : allOf + nullable
        out = {"anyOf": [{"allOf": out["allOf"]}, {"type": "null"}]}
    if "properties" in out and "additionalProperties" not in out:
        out["additionalProperties"] = False
    return out


def check(doc, template, method, status, payload):
    """Valide `payload` contre la réponse `status` de l'opération `method template`."""
    op = doc["paths"][template][method]
    declared = op["responses"][str(status)]["content"]["application/json"]["schema"]
    schema_ = _inline(declared, doc["components"]["schemas"])
    errors = sorted(Draft7Validator(schema_).iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, f"{method.upper()} {template} → {status} : " + " ; ".join(
        f"{'/'.join(map(str, e.path)) or '(racine)'} : {e.message[:150]}" for e in errors[:5]
    )


def test_the_checker_catches_an_undocumented_key_a_wrong_type_and_a_missing_key(doc):
    good = {"version": "1.0.0", "digest": "abc"}
    check(doc, f"{W}/control-library/{{slug}}/publish/", "post", 201, good)
    for bad in ({**good, "extra": 1}, {"version": 1, "digest": "abc"}, {"version": "1.0.0"}):
        with pytest.raises(AssertionError):
            check(doc, f"{W}/control-library/{{slug}}/publish/", "post", 201, bad)


def test_control_library_responses(world, doc):
    c = client_for(world["designer"])
    url = f"{BASE}/control-library/"
    r = c.post(
        url, {"slug": "adresse", "name": "Adresse", "spec": ADDRESS, "icon": "home", "tags": ["ui"]}, format="json"
    )
    assert r.status_code == 201
    check(doc, f"{W}/control-library/", "post", 201, r.json())
    check(doc, f"{W}/control-library/", "get", 200, c.get(url).json())
    d = c.get(f"{url}adresse/")
    check(doc, f"{W}/control-library/{{slug}}/", "get", 200, d.json())
    p = c.patch(f"{url}adresse/", {"spec": ADDRESS, "revision": 1}, format="json")
    assert p.status_code == 200
    check(doc, f"{W}/control-library/{{slug}}/", "patch", 200, p.json())
    pub = c.post(f"{url}adresse/publish/", {"changelog": "première"}, format="json")
    assert pub.status_code == 201
    check(doc, f"{W}/control-library/{{slug}}/publish/", "post", 201, pub.json())
    # une version publiée : la fiche porte `published`, la liste `used_by`, le détail `versions`
    check(doc, f"{W}/control-library/", "get", 200, c.get(f"{url}?published=1").json())
    check(doc, f"{W}/control-library/{{slug}}/", "get", 200, c.get(f"{url}adresse/").json())
    chk = c.post(f"{url}check/", {"spec": ADDRESS}, format="json")
    assert chk.status_code == 200
    check(doc, f"{W}/control-library/check/", "post", 200, chk.json())
    bad = c.post(
        f"{url}check/",
        {"spec": {"version": 1, "mode": "group", "parts": [{"key": "a", "label": "A", "type": "datagrid"}]}},
        format="json",
    )
    check(doc, f"{W}/control-library/check/", "post", 200, bad.json())  # avec des problèmes


def test_form_library_responses(world, doc):
    c = client_for(world["designer"])
    url = f"{BASE}/form-library/"
    d = definition(field("sujet", required=True))
    r = c.post(url, {"slug": "demande", "name": "Demande", "tags": ["rh"], "definition": d}, format="json")
    assert r.status_code == 201
    check(doc, f"{W}/form-library/", "post", 201, r.json())
    check(doc, f"{W}/form-library/", "get", 200, c.get(url).json())
    check(doc, f"{W}/form-library/{{slug}}/", "get", 200, c.get(f"{url}demande/").json())
    p = c.patch(f"{url}demande/", {"definition": d, "revision": 1}, format="json")
    assert p.status_code == 200
    check(doc, f"{W}/form-library/{{slug}}/", "patch", 200, p.json())
    pub = c.post(f"{url}demande/publish/", {"changelog": "première"}, format="json")
    assert pub.status_code == 201
    check(doc, f"{W}/form-library/{{slug}}/publish/", "post", 201, pub.json())
    check(doc, f"{W}/form-library/", "get", 200, c.get(url).json())
    check(doc, f"{W}/form-library/{{slug}}/", "get", 200, c.get(f"{url}demande/").json())
    chk = c.post(f"{url}check/", {"definition": d, "css": "& .x { color: red; }"}, format="json")
    assert chk.status_code == 200
    check(doc, f"{W}/form-library/check/", "post", 200, chk.json())
    only_def = c.post(f"{url}check/", {"definition": d}, format="json")
    check(doc, f"{W}/form-library/check/", "post", 200, only_def.json())


def test_workflow_publish_and_run_start_responses(world, doc):
    ws = world["ws_a"]
    for slug in ACTIONS:
        make_action(ws, slug)
    c = client_for(world["designer"])
    base = f"{BASE}/workflows/"
    assert c.post(base, {"slug": "pub", "name": "Pub"}, format="json").status_code == 201
    graph = {"inputs": schema(), "nodes": [node("a", action="echo", inputs={"value": "x"})], "edges": [], "outputs": {}}
    assert c.put(base + "pub/draft/", {"graph": graph, "revision": 1}, format="json").status_code == 200
    pub = c.post(base + "pub/publish/", {"changelog": "première"}, format="json")
    assert pub.status_code == 201
    check(doc, f"{W}/workflows/{{slug}}/publish/", "post", 201, pub.json())
    started = c.post(
        base + "pub/runs/", {"environment": "test", "inputs": {}}, format="json", HTTP_IDEMPOTENCY_KEY="k-1"
    )
    assert started.status_code == 202
    check(doc, f"{W}/workflows/{{slug}}/runs/", "post", 202, started.json())
    again = c.post(base + "pub/runs/", {"environment": "test", "inputs": {}}, format="json", HTTP_IDEMPOTENCY_KEY="k-1")
    assert again.status_code == 200  # même clé : l'exécution existante
    check(doc, f"{W}/workflows/{{slug}}/runs/", "post", 200, again.json())


def test_catalog_run_start_response(world, doc):
    ws = world["ws_a"]
    publish(ws, "ouvert", [{"id": "a", "type": "wait", "name": "Pause", "seconds": 1}], [], inputs=schema())
    ws.workflows.filter(slug="ouvert").update(in_catalog=True)
    admin = client_for(make_user("ada", ws, Role.ADMIN))
    cathy = client_for(make_user("cathy", ws, Role.CATALOG))
    assert (
        admin.post(
            f"{BASE}/groups/", {"name": "Compta", "members": ["cathy"], "workflows": ["ouvert"]}, format="json"
        ).status_code
        == 201
    )
    r = cathy.post(f"{BASE}/catalog/ouvert/runs/", {"environment": "test", "inputs": {}}, format="json")
    assert r.status_code == 202
    check(doc, f"{W}/catalog/{{slug}}/runs/", "post", 202, r.json())


def test_skill_version_response(world, doc):
    ws = world["ws_a"]
    AiPolicy.objects.update_or_create(workspace=ws, defaults={"mode": "local_strict"})
    AiProvider.objects.create(workspace=ws, name="fake", kind="fake")
    c = client_for(world["designer"])
    assert c.post(f"{BASE}/ai/skills/", body(), format="json").status_code == 201
    r = c.post(
        f"{BASE}/ai/skills/qualifier/versions/",
        {"instructions": "Nouveau {{ inputs.texte }}", "output_schema": OUT, "input_schema": IN, "bump": "patch"},
        format="json",
    )
    assert r.status_code == 201
    check(doc, f"{W}/ai/skills/{{slug}}/versions/", "post", 201, r.json())
