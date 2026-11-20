"""Documentation générée d'un produit de l'API store : contenu dérivé de l'état réel, droits, absence de secret."""

import json

import pytest

from apistore import docs
from apistore.models import Product, ProductWorkflow
from tenancy.models import Role
from tests.apistore_helpers import active_key
from tests.conftest import client_for, make_user, make_workspace
from workflows.models import Workflow, WorkflowVersion

M = "/api/v1/plugins/apistore/manage/store"
SCHEMA = {
    "type": "object",
    "required": ["nom", "niveau"],
    "properties": {
        "nom": {"type": "string", "description": "Nom du client", "maxLength": 40},
        "niveau": {"type": "string", "enum": ["bas", "haut"]},
        "quantite": {"type": "integer", "minimum": 3, "default": 5},
        "tags": {"type": "array", "items": {"type": "string"}},
        "actif": {"type": "boolean"},
    },
}


@pytest.fixture
def documented(store):
    """Le workflow « hello » du produit reçoit un schéma d'entrée et deux sorties ; un second workflow n'est pas publié."""
    ws, owner = store["ws"], store["owner"]
    wf = ws.workflows.get(slug="hello")
    base = wf.latest_version()
    WorkflowVersion.objects.create(
        workflow=wf,
        version="1.1.0",
        graph={**base.graph, "outputs": {"resultat": "{{ x }}", "alpha": "{{ y }}"}},
        input_schema=SCHEMA,
        created_by=owner,
    )
    brouillon = Workflow.objects.create(workspace=ws, slug="sans-version", name="Sans version")
    ProductWorkflow.objects.create(product=store["product"], workflow=brouillon)
    return store


def test_the_page_describes_every_published_workflow_with_its_real_input_schema(documented):
    r = client_for(documented["asker"]).get(f"{M}/products/p1/docs/")
    assert r.status_code == 200
    d = r.json()
    run = next(e for e in d["endpoints"] if e["id"] == "run-hello")
    assert run["method"] == "POST" and run["path"] == "/api/v1/plugins/apistore/v1/store/p1/workflows/hello/runs/"
    assert run["version"] == "1.1.0" and run["input_schema"] == SCHEMA
    by_name = {p["name"]: p for p in run["parameters"]}
    assert by_name["nom"]["required"] and by_name["nom"]["constraints"] == ["au plus 40 caractères"]
    assert by_name["niveau"]["enum"] == ["bas", "haut"] and by_name["niveau"]["required"]
    assert not by_name["quantite"]["required"] and by_name["quantite"]["default"] == 5  # défaut : jamais obligatoire
    assert by_name["tags"]["type"] == "liste de string"
    assert run["outputs"] == ["alpha", "resultat"]
    assert d["unavailable"] == ["sans-version"] and all(e["workflow"] != "sans-version" for e in d["endpoints"])
    assert [e["id"] for e in d["endpoints"]][-1] == "run-status"
    assert d["environments"] == ["test", "prod"] and {p["slug"] for p in d["plans"]} == {"libre", "pro"}


def test_the_request_example_is_valid_for_the_schema(documented):
    from runs.serializers import validate_inputs_against, with_defaults

    d = client_for(documented["asker"]).get(f"{M}/products/p1/docs/").json()
    run = next(e for e in d["endpoints"] if e["id"] == "run-hello")
    ex = run["request_example"]
    assert ex["environment"] == "test"
    assert ex["inputs"]["niveau"] == "bas" and ex["inputs"]["quantite"] == 5
    assert validate_inputs_against(SCHEMA, with_defaults(SCHEMA, ex["inputs"])) == {}


def test_examples_carry_placeholders_never_a_real_key_or_host(documented):
    _sub, _key, raw = active_key(documented)
    d = client_for(documented["asker"]).get(f"{M}/products/p1/docs/").json()
    text = json.dumps(d)
    assert raw not in text and "fxs_VOTRE_CLE" in text
    run = next(e for e in d["endpoints"] if e["id"] == "run-hello")
    assert run["examples"]["curl"].startswith(
        "curl -X POST <hôte>/api/v1/plugins/apistore/v1/store/p1/workflows/hello/runs/"
    )
    assert "Idempotency-Key" in run["examples"]["curl"] and "urllib.request" in run["examples"]["python"]
    assert "fetch(" in run["examples"]["javascript"] and "JSON.stringify" in run["examples"]["javascript"]
    status = next(e for e in d["endpoints"] if e["id"] == "run-status")
    assert "-d " not in status["examples"]["curl"] and "data=" not in status["examples"]["python"]


def test_a_quote_in_an_example_value_cannot_break_out_of_the_curl_command():
    out = docs.snippets("POST", "<hôte>/x", {"inputs": {"t": "l'été'; rm -rf /"}}, idempotent=False)["curl"]
    assert "-d '{\"inputs\": {\"t\": \"l'\\''été'\\''; rm -rf /\"}}'" in out


def test_a_draft_product_is_documented_for_administrators_only(documented):
    Product.objects.filter(slug="p1").update(status="draft")
    admin = make_user("boss", documented["ws"], Role.ADMIN)
    assert client_for(admin).get(f"{M}/products/p1/docs/").status_code == 200
    assert client_for(documented["asker"]).get(f"{M}/products/p1/docs/").status_code == 404
    assert client_for(documented["asker"]).get(f"{M}/products/p1/docs/openapi/").status_code == 404
    assert client_for(admin).get(f"{M}/products/inconnu/docs/").status_code == 404


def test_the_documentation_needs_a_session_or_token_and_membership(documented):
    from rest_framework.test import APIClient

    assert APIClient().get(f"{M}/products/p1/docs/").status_code in (401, 403)
    viewer = make_user("autre", documented["ws"], Role.VIEWER)
    assert client_for(viewer).get(f"{M}/products/p1/docs/").status_code == 200  # un lecteur voit les produits publiés
    other = make_workspace("autre-espace")
    stranger = make_user("etranger", other, Role.ADMIN)
    assert client_for(stranger).get(f"{M}/products/p1/docs/").status_code == 404


def test_the_openapi_document_follows_the_page(documented):
    r = client_for(documented["asker"]).get(f"{M}/products/p1/docs/openapi/")
    assert r.status_code == 200
    o = r.json()
    assert o["openapi"] == "3.1.0" and o["components"]["securitySchemes"]["cle"]["scheme"] == "bearer"
    run = o["paths"]["/api/v1/plugins/apistore/v1/store/p1/workflows/hello/runs/"]["post"]
    body = run["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["inputs"] == SCHEMA and body["properties"]["environment"]["enum"] == ["test", "prod"]
    assert {"202", "200", "401", "429"} <= set(run["responses"])
    status = o["paths"]["/api/v1/plugins/apistore/v1/store/p1/runs/{run_id}/"]["get"]
    assert status["parameters"][0]["in"] == "path"


def test_example_values_follow_the_schema():
    assert docs.example_value({"type": "string", "format": "email"}) == "nom@exemple.fr"
    assert docs.example_value({"enum": ["a", "b"], "type": "string"}) == "a"
    assert docs.example_value({"type": "array", "items": {"type": "integer"}}) == [1]
    assert docs.example_value({"type": "object", "properties": {"a": {"type": "boolean"}}}) == {"a": True}
    assert docs.example_value({"type": ["null", "number"], "minimum": 2}) == 2.0
    assert docs.example_value({"type": "integer", "minimum": 7}) == 7
    assert docs.example_value("pas un objet") is None
    assert docs.parameters({}) == [] and docs.output_fields(None) == []
