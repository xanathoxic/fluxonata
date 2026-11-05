import pytest
from rest_framework.test import APIClient

from runs.models import WorkflowRun
from tests.conftest import client_for
from tests.form_helpers import make_form
from tests.wf_helpers import ACTIONS, make_action, node, publish, schema
from triggers import formengine
from triggers.models import PublicForm

BASE = "/api/v1/workspaces/team-a"


def field(key, type="text", **extra):
    return {"key": key, "label": key.capitalize(), "type": type, **extra}


def definition(*fields):
    return {"version": 1, "sections": [{"id": "s1", "title": "Demande", "fields": list(fields)}]}


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def form(ws, world):
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=schema(["sujet"], sujet={"type": "string"}, motif={"type": "string"}, age={"type": "integer"}),
        user=world["designer"],
    )
    r = client_for(world["designer"]).post(
        f"{BASE}/forms/",
        {"name": "Demande", "title": "Demande", "workflow": "demande", "environment": "test"},
        format="json",
    )
    return PublicForm.objects.get(pk=r.data["id"])


def test_structure_checks_refuse_malformed_definitions():
    assert formengine.check_definition(definition(field("a"), field("b", "select", options=[{"value": "x"}]))) == {}
    bad = {
        "type inconnu": definition(field("a", "password")),
        "clé en double": definition(field("a"), field("a")),
        "clé invalide": definition(field("1a")),
        "options manquantes": definition(field("a", "select")),
        "options sur un texte": definition(field("a", "text", options=[{"value": "x"}])),
        "règle sur soi-même": definition(field("a", visible_if="values.a == 1")),
        "règle en boucle": definition(field("a", visible_if="values.b == 1"), field("b", visible_if="values.a == 1")),
        "règle hors formulaire": definition(field("a"), field("b", visible_if="inputs.a == 1")),
        "règle invalide": definition(field("a"), field("b", visible_if="values.a ==")),
        "champ inconnu": definition({**field("a"), "script": "x"}),
    }
    for name, d in bad.items():
        assert formengine.check_definition(d), name
    assert formengine.check_definition({"sections": []})


def test_visibility_drops_hidden_values_and_validates_visible_ones():
    d = definition(
        field("type", "select", options=[{"value": "a"}, {"value": "autre"}], required=True),
        field("precision", required=True, visible_if="values.type == 'autre'"),
        field("mail", "email"),
        field("n", "number", min=1, max=5),
    )
    kept, errors = formengine.validate_submission(d, {"type": "a", "precision": "secret", "inconnu": 1})
    assert kept == {"type": "a"} and errors == {}
    kept, errors = formengine.validate_submission(d, {"type": "autre", "mail": "x", "n": 9})
    assert set(errors) == {"precision", "mail", "n"}
    assert formengine.validate_submission(d, {"type": "zzz"})[1] == {"type": "Choix non proposé."}


def test_link_with_the_workflow_inputs():
    schema_ = schema(["sujet"], sujet={"type": "string"}, age={"type": "integer"})
    ok = definition(field("sujet"), field("age", "number"))
    assert formengine.check_against_inputs(ok, schema_) == {}
    assert "sujet" in formengine.check_against_inputs(definition(field("age", "number")), schema_)
    assert "x" in formengine.check_against_inputs(definition(field("sujet"), field("x")), schema_)
    assert "age" in formengine.check_against_inputs(definition(field("sujet"), field("age", "checkbox")), schema_)
    hidden = definition(field("a"), field("sujet", visible_if="values.a == 'x'"))
    assert "sujet" in formengine.check_against_inputs(hidden, schema_)


def test_attach_flow_and_public_submission(form, ws, world):
    c = client_for(world["designer"])
    url = f"{BASE}/forms/{form.pk}/"
    d = definition(
        field("sujet", required=True),
        field("motif", "select", options=[{"value": "panne"}, {"value": "autre"}]),
        field("age", "number", min=18),
    )
    make_form(world["designer"], "saisie", d)
    anon = APIClient()
    assert "definition" not in anon.get(f"/api/v1/forms/{form.token}/").data  # still the legacy page: nothing attached
    attached = c.post(url + "attach/", {"form": "saisie"}, format="json")
    assert (
        attached.status_code == 200
        and attached.data["library_form"] == "saisie"
        and attached.data["library_version"] == "1.0.0"
    )
    page = anon.get(f"/api/v1/forms/{form.token}/")
    assert page.data["definition"] == d and "schema" not in page.data
    bad = anon.post(f"/api/v1/forms/{form.token}/", {"values": {"motif": "x", "age": 3}}, format="json")
    assert bad.status_code == 400 and set(bad.data["error"]["details"]) == {"sujet", "motif", "age"}
    ok = anon.post(
        f"/api/v1/forms/{form.token}/",
        {"values": {"sujet": "Panne", "motif": "panne", "age": 30, "x": 1}},
        format="json",
    )
    assert ok.status_code == 201
    run = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="demande")
    assert run.inputs == {"sujet": "Panne", "motif": "panne", "age": 30}
    assert c.delete(url + "attach/").status_code == 200
    assert "definition" not in anon.get(f"/api/v1/forms/{form.token}/").data


def test_a_public_form_serves_its_attached_version_not_later_ones(form, world):
    c = make_form(world["designer"], "saisie", definition(field("sujet", required=True)))
    c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "saisie"}, format="json")
    c.patch(
        f"{BASE}/form-library/saisie/",
        {"definition": definition(field("sujet"), field("motif")), "revision": 1},
        format="json",
    )
    c.post(f"{BASE}/form-library/saisie/publish/", {}, format="json")
    page = APIClient().get(f"/api/v1/forms/{form.token}/")
    assert [f["key"] for f in page.data["definition"]["sections"][0]["fields"]] == ["sujet"]  # 1.0.0, still
    c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "saisie", "version": "1.1.0"}, format="json")
    page = APIClient().get(f"/api/v1/forms/{form.token}/")
    assert [f["key"] for f in page.data["definition"]["sections"][0]["fields"]] == ["sujet", "motif"]
    assert c.get(f"{BASE}/form-library/saisie/").data["usage"]["public_forms"][0]["version"] == "1.1.0"


def test_attaching_refuses_a_form_that_does_not_match_the_workflow(form, world):
    c = make_form(world["designer"], "autre", definition(field("zzz")))
    r = c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "autre"}, format="json")
    assert r.status_code == 400 and "zzz" in r.json()["error"]["details"]
    assert c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "inconnu"}, format="json").status_code == 400
    make_form(world["designer"], "brouillon", definition(field("sujet")), publish=False)
    assert c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "brouillon"}, format="json").status_code == 400
    make_form(world["designer"], "modele", definition(field("sujet")), publish=False, is_template=True)
    assert c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "modele"}, format="json").status_code == 400


def test_only_publishers_attach_and_workspaces_are_isolated(form, world):
    make_form(world["designer"], "saisie", definition(field("sujet", required=True)))
    url = f"{BASE}/forms/{form.pk}/attach/"
    assert client_for(world["viewer"]).post(url, {"form": "saisie"}, format="json").status_code == 403
    assert client_for(world["outsider"]).post(url, {"form": "saisie"}, format="json").status_code == 404


def test_a_correspondence_chooses_which_field_feeds_each_input():
    schema_ = schema(["sujet"], sujet={"type": "string"}, motif={"type": "string"}, age={"type": "integer"})
    d = definition(field("objet"), field("raison"), field("annee", "number"), field("commentaire"))
    mapping = {"sujet": "objet", "motif": "raison", "age": "annee"}
    assert formengine.check_against_inputs(d, schema_, mapping) == {}  # a field that feeds nothing is allowed
    assert "commentaire" in formengine.check_against_inputs(d, schema_)  # …but not without any correspondence
    bad = formengine.check_against_inputs(d, schema_, {**mapping, "sujet": "absent", "age": "raison"})
    assert "absent" in bad["sujet"] and "integer" in bad["age"]
    assert "zzz" in formengine.check_against_inputs(d, schema_, {**mapping, "zzz": "objet"})
    assert formengine.check_against_inputs(d, schema_, {**mapping, "sujet": ""})["sujet"].startswith(
        "L'entrée obligatoire"
    )
    assert formengine.check_against_inputs(d, schema_, {**mapping, "motif": ""}) == {}  # optional: left empty
    hidden = definition(field("a"), field("objet", visible_if="values.a == 'x'"), field("annee", "number"))
    assert "sujet" in formengine.check_against_inputs(hidden, schema_, {"sujet": "objet", "age": "annee"})
    # unlisted inputs still take the field of the same name
    same = definition(field("sujet"), field("annee", "number"))
    assert formengine.check_against_inputs(same, schema_, {"age": "annee"}) == {}
    values = {"objet": "Panne", "annee": 30, "commentaire": "x"}
    assert formengine.map_inputs(d, schema_, mapping, values) == {"sujet": "Panne", "age": 30}
    assert formengine.map_inputs(d, schema_, None, values) is values
    assert formengine.input_fields(d, schema_, mapping) == mapping
    assert formengine.check_definition({**d, "map": mapping}) == {}
    assert formengine.check_definition({**d, "map": {"a": 1}}) and formengine.check_definition({**d, "map": []})


def test_a_public_form_with_a_correspondence_starts_the_run_with_the_mapped_inputs(form, ws, world):
    c = client_for(world["designer"])
    url = f"{BASE}/forms/{form.pk}/"
    d = definition(field("objet", required=True), field("raison"), field("annee", "number"), field("commentaire"))
    make_form(world["designer"], "autres-noms", d)
    mapping = {"sujet": "objet", "motif": "raison", "age": "annee"}
    assert c.post(url + "attach/", {"form": "autres-noms"}, format="json").status_code == 400  # names differ
    wrong = c.post(url + "attach/", {"form": "autres-noms", "input_map": {**mapping, "sujet": "x"}}, format="json")
    assert wrong.status_code == 400 and "sujet" in wrong.json()["error"]["details"]
    assert c.post(url + "attach/", {"form": "autres-noms", "input_map": {"sujet": 3}}, format="json").status_code == 400
    assert c.post(url + "attach/", {"form": "autres-noms", "input_map": mapping}, format="json").status_code == 200
    anon = APIClient()
    ok = anon.post(
        f"/api/v1/forms/{form.token}/",
        {"values": {"objet": "Panne", "raison": "usure", "annee": 30, "commentaire": "ignoré"}},
        format="json",
    )
    assert ok.status_code == 201
    run = WorkflowRun.objects.get(workspace=ws, workflow_version__workflow__slug="demande")
    assert run.inputs == {"sujet": "Panne", "motif": "usure", "age": 30}
    form.refresh_from_db()
    assert form.input_map == mapping
    assert c.delete(url + "attach/").status_code == 200
    form.refresh_from_db()
    assert form.input_map == {}


def test_the_workflow_keeps_its_correspondence_when_published_and_reuses_it_for_the_public_form(form, ws, world):
    from workflows.validation import _validate_input_form

    d = definition(field("objet", required=True), field("raison"), field("annee", "number"))
    make_form(world["designer"], "autres-noms", d)
    inputs = schema(["sujet"], sujet={"type": "string"}, motif={"type": "string"}, age={"type": "integer"})
    ref = {"form": "autres-noms", "version": None}
    mapping = {"sujet": "objet", "motif": "raison", "age": "annee"}
    assert _validate_input_form({"ref": ref, "map": mapping}, inputs, ws) == []
    assert _validate_input_form({"ref": ref}, inputs, ws)  # without it the names differ
    assert _validate_input_form({"ref": ref, "map": {"sujet": "absent"}}, inputs, ws)
    assert _validate_input_form({"ref": ref, "map": {"1x": "objet"}}, inputs, ws)
    publish(
        ws,
        "demande",
        [node("e", action="echo", inputs={"value": "{{ inputs.sujet }}"})],
        [],
        inputs=inputs,
        user=world["designer"],
        input_form={"ref": ref, "map": mapping},
    )
    from workflows.models import Workflow

    pinned = Workflow.objects.get(workspace=ws, slug="demande").latest_version().graph["input_form"]
    assert pinned["map"] == mapping and pinned["ref"]["form"] == "autres-noms" and "sections" in pinned
    # the public form takes the workflow's correspondence for this same form when none is given
    c = client_for(world["designer"])
    assert c.post(f"{BASE}/forms/{form.pk}/attach/", {"form": "autres-noms"}, format="json").status_code == 200
    form.refresh_from_db()
    assert form.input_map == mapping
