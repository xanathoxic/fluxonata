"""MCP de conception : contrôles personnalisés (tous les types), accès complet aux formulaires (style, CSS, grille, calculs, listes dynamiques, contrôles)
et ajout d'un contrôle à un formulaire. Un assistant prépare des BROUILLONS ; il ne publie jamais."""

import pytest

from audit.models import AuditEvent
from formbuilder.models import Control, ControlVersion, Form
from tests.conftest import client_for
from tests.test_mcp_design_write import FORM_DEF, call, error_text
from tests.wf_helpers import ACTIONS, make_action, node, schema
from triggers import formcontrols, formcss, formengine
from workflows.mcp_design_guide import CONTROL_EXAMPLES, FORM_FULL_EXAMPLE, guide
from workflows.mcp_design_write import ASSISTANT_TAG
from workflows.models import FolderAccess, FolderGrant, Workflow

WS = "/api/v1/workspaces/team-a"
MODES = {
    "group": "group",
    "repeat": "repeat",
    "repeat_table": "repeat",
    "items": "items",
    "choice": "choice",
    "nested": "repeat",
}


@pytest.fixture
def ws(world):
    for slug in ACTIONS:
        make_action(world["ws_a"], slug)
    return world["ws_a"]


@pytest.fixture
def c(world):
    return client_for(world["designer"])


def make(c, slug="adresse", spec=None, **extra):
    res, data = call(
        c, "create_control", {"slug": slug, "name": slug.title(), "spec": spec or CONTROL_EXAMPLES["group"], **extra}
    )
    assert not res["isError"], res
    return data


def publish(c, slug):
    r = c.post(f"{WS}/control-library/{slug}/publish/", {}, format="json")
    assert r.status_code == 201, r.content
    return r.json()["version"]


def new_form(c, slug="cmd", definition=None):
    res, data = call(c, "create_form", {"slug": slug, "name": slug.title(), "definition": definition or FORM_DEF})
    assert not res["isError"], res
    return data


# --- les exemples du guide ---------------------------------------------------------------------------------------------------------------
def test_every_example_of_the_guide_is_accepted_by_the_server():
    for name, spec in CONTROL_EXAMPLES.items():
        assert formcontrols.check_spec(spec, draft=False) == {}, name
    assert formengine.check_definition(FORM_FULL_EXAMPLE) == {}
    text = guide("controles")["guide"]
    for tool in (
        "list_controls",
        "create_control",
        "update_control",
        "validate_control",
        "add_control_to_form",
        "validate_form",
    ):
        assert tool in text
    assert "controles" in guide("all")["examples"] or "controls" in guide("all")["examples"]
    assert "STYLE" in guide("form")["guide"] and "CSS" in guide("form")["guide"] and "GRILLE" in guide("form")["guide"]


# --- création : tous les types -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(MODES))
def test_a_control_of_every_type_is_created_as_a_marked_unpublished_draft(ws, c, name):
    data = make(c, f"ctl-{name.replace('_', '-')}", CONTROL_EXAMPLES[name])
    assert data["mode"] == MODES[name] and data["revision"] == 1 and "brouillon" in data["status"]
    control = Control.objects.get(slug=f"ctl-{name.replace('_', '-')}")
    assert ASSISTANT_TAG in control.tags and control.draft_spec == CONTROL_EXAMPLES[name]
    assert not ControlVersion.objects.filter(control=control).exists()  # jamais publié par l'assistant
    assert AuditEvent.objects.filter(
        action="mcp.design.write", data__tool="create_control", data__slug=control.slug
    ).exists()


def test_there_is_no_tool_to_publish_or_delete_a_control(ws, c):
    make(c)
    for tool in ("publish_control", "delete_control", "publish_form", "delete_form"):
        assert "inconnu" in error_text(call(c, tool, {"slug": "adresse"})[0]).lower()
    assert Control.objects.filter(slug="adresse").exists() and not ControlVersion.objects.exists()
    assert AuditEvent.objects.filter(action="mcp.design.refused", data__reason="unknown_tool").count() == 4


def test_invalid_controls_are_refused_with_the_server_message_and_nothing_is_written(ws, c):
    bad = {"version": 1, "mode": "cercle", "parts": []}
    assert "mode" in error_text(call(c, "create_control", {"slug": "x", "name": "X", "spec": bad})[0]).lower()
    too_many = {
        "version": 1,
        "mode": "group",
        "parts": [{"key": f"k{i}", "type": "text", "label": "L"} for i in range(31)],
    }
    assert "30" in error_text(call(c, "create_control", {"slug": "y", "name": "Y", "spec": too_many})[0])
    dyn = {
        "version": 1,
        "mode": "group",
        "parts": [{"key": "a", "type": "select", "label": "A", "source": {"connection": "c", "path": "/x"}}],
    }
    assert error_text(call(c, "create_control", {"slug": "z", "name": "Z", "spec": dyn})[0])
    assert not Control.objects.exists()
    make(c)
    assert "existe déjà" in error_text(call(c, "create_control", {"slug": "adresse", "name": "Bis"})[0])


def test_private_folders_and_rights_apply(ws, world):
    acc = FolderAccess.objects.create(workspace=ws, folder="RH")
    FolderGrant.objects.create(access=acc, user=world["viewer"], level="view")  # le concepteur n'est PAS dans la liste
    designer, viewer = client_for(world["designer"]), client_for(world["viewer"])
    assert (
        "Droit" in error_text(call(designer, "create_control", {"slug": "rh", "name": "RH", "folder": "RH"})[0])
        or not Control.objects.exists()
    )
    Control.objects.create(
        workspace=ws, slug="secret", name="Secret", folder="RH", draft_spec=CONTROL_EXAMPLES["group"]
    )
    assert call(designer, "list_controls")[1]["count"] == 0  # caché, sans fuite
    assert "Introuvable" in error_text(call(designer, "get_control", {"slug": "secret"})[0])
    assert call(viewer, "list_controls")[1]["count"] == 1
    # la lecture est ouverte à qui voit l'espace ; l'écriture est refusée par les droits de la personne, au moment de l'appel
    assert "modifier" in error_text(call(viewer, "create_control", {"slug": "v", "name": "V"})[0])
    assert "modifier" in error_text(
        call(viewer, "add_control_to_form", {"form": "x", "revision": 1, "control": "secret"})[0]
    )
    assert not Control.objects.filter(slug="v").exists()


# --- lecture et modification ---------------------------------------------------------------------------------------------------------------
def test_list_and_get_show_the_draft_and_the_published_version(ws, c):
    make(c)
    row = call(c, "list_controls", {"query": "adresse"})[1]["results"][0]
    assert row["mode"] == "group" and row["published_version"] is None and "brouillon" in row["status"]
    detail = call(c, "get_control", {"slug": "adresse"})[1]
    assert (
        detail["draft_spec"] == CONTROL_EXAMPLES["group"]
        and detail["published"] is None
        and detail["draft_revision"] == 1
    )
    publish(c, "adresse")  # c'est une PERSONNE qui publie, par l'API
    detail = call(c, "get_control", {"slug": "adresse"})[1]
    assert detail["published"]["version"] == "1.0.0" and detail["published"]["spec"] == CONTROL_EXAMPLES["group"]
    assert call(c, "list_controls")[1]["results"][0]["status"] == "publié"


def test_update_needs_the_current_revision_and_never_changes_the_type(ws, c):
    make(c)
    extra_part = {"key": "pays", "label": "Pays", "type": "text", "layout": {"row": 3, "col": 1, "span": 12}}
    wider = {**CONTROL_EXAMPLES["group"], "parts": [*CONTROL_EXAMPLES["group"]["parts"], extra_part]}
    ok = call(c, "update_control", {"slug": "adresse", "revision": 1, "spec": wider, "name": "Adresse postale"})[1]
    assert ok["revision"] == 2
    assert Control.objects.get(slug="adresse").name == "Adresse postale"
    stale = call(c, "update_control", {"slug": "adresse", "revision": 1, "spec": CONTROL_EXAMPLES["group"]})[0]
    assert "modifié ailleurs" in error_text(stale)
    assert "changé" in error_text(
        call(c, "update_control", {"slug": "adresse", "revision": 2, "spec": CONTROL_EXAMPLES["repeat"]})[0]
    )
    assert Control.objects.get(slug="adresse").draft_revision == 2
    only_name = call(c, "update_control", {"slug": "adresse", "revision": 2, "description": "Adresse de livraison"})[1]
    assert only_name["revision"] == 2  # sans `spec`, la révision du brouillon ne bouge pas


def test_validate_control_writes_nothing_and_tells_draft_from_publishable(ws, c):
    empty = {"version": 1, "mode": "group", "parts": []}
    res = call(c, "validate_control", {"spec": empty})[1]
    assert res["valid_as_draft"] is True and res["publishable"] is False and res["publish_problems"]
    assert call(c, "validate_control", {"spec": CONTROL_EXAMPLES["nested"]})[1] | {} == {
        "valid_as_draft": True,
        "problems": {},
        "publishable": True,
        "publish_problems": {},
    }
    assert (
        call(c, "validate_control", {"spec": {"version": 1, "mode": "zzz", "parts": []}})[1]["valid_as_draft"] is False
    )
    assert not Control.objects.exists()


# --- ajouter un contrôle à un formulaire -------------------------------------------------------------------------------------------------
def test_a_published_control_is_added_with_its_latest_version(ws, c):
    make(c)
    publish(c, "adresse")
    form = new_form(c)
    res = call(c, "add_control_to_form", {"form": "cmd", "revision": form["revision"], "control": "adresse"})[1]
    assert (
        res["control_version"] == "1.0.0"
        and res["key"] == "adresse_1"
        and res["revision"] == form["revision"] + 1
        and "warning" not in res
    )
    field = Form.objects.get(slug="cmd").draft_definition["sections"][0]["fields"][-1]
    assert field["type"] == "custom" and field["control"]["ref"] == "adresse" and field["control"]["version"] == "1.0.0"
    assert (
        field["control"]["spec"] == CONTROL_EXAMPLES["group"]
        and formengine.check_definition(Form.objects.get(slug="cmd").draft_definition) == {}
    )


def test_an_unpublished_control_is_copied_as_a_draft_with_a_warning_and_the_form_stays_valid(ws, c):
    make(c, "invites", CONTROL_EXAMPLES["nested"])
    form = new_form(c)
    res = call(
        c,
        "add_control_to_form",
        {"form": "cmd", "revision": form["revision"], "control": "invites", "key": "liste_invites"},
    )[1]
    assert res["control_version"] == "0.0.0" and "NON publié" in res["warning"]
    definition = Form.objects.get(slug="cmd").draft_definition
    assert formengine.check_definition(definition) == {}
    assert (
        next(f for f in definition["sections"][0]["fields"] if f["key"] == "liste_invites")["control"]["spec"]
        == CONTROL_EXAMPLES["nested"]
    )
    assert not ControlVersion.objects.exists()  # l'ajout au formulaire ne publie rien


def test_keys_sections_layout_and_field_settings(ws, c):
    make(c)
    publish(c, "adresse")
    grid = {
        "version": 1,
        "sections": [
            {
                "id": "a",
                "title": "A",
                "fields": [{"key": "nom", "type": "text", "label": "Nom", "layout": {"row": 1, "col": 1, "span": 12}}],
            },
            {"id": "b", "title": "B", "fields": []},
        ],
    }
    rev = new_form(c, "grille", grid)["revision"]
    r1 = call(
        c,
        "add_control_to_form",
        {
            "form": "grille",
            "revision": rev,
            "control": "adresse",
            "section": "a",
            "field": {"required": True, "label": "Livraison"},
        },
    )[1]
    f = Form.objects.get(slug="grille").draft_definition["sections"][0]["fields"][-1]
    assert (
        f["layout"] == {"row": 2, "col": 1, "span": 12} and f["required"] is True and f["label"] == "Livraison"
    )  # rangé SOUS les champs placés
    r2 = call(
        c, "add_control_to_form", {"form": "grille", "revision": r1["revision"], "control": "adresse", "section": "1"}
    )[1]
    assert (
        r2["key"] == "adresse_2"
        and Form.objects.get(slug="grille").draft_definition["sections"][1]["fields"][0]["key"]
        == "adresse_2"  # unique dans TOUT le formulaire
    )
    dup = call(
        c, "add_control_to_form", {"form": "grille", "revision": r2["revision"], "control": "adresse", "key": "nom"}
    )[0]
    assert "existe déjà" in error_text(dup)
    assert "Section" in error_text(
        call(
            c,
            "add_control_to_form",
            {"form": "grille", "revision": r2["revision"], "control": "adresse", "section": "zzz"},
        )[0]
    )
    assert "Réglages permis" in error_text(
        call(
            c,
            "add_control_to_form",
            {"form": "grille", "revision": r2["revision"], "control": "adresse", "field": {"type": "text"}},
        )[0]
    )


def test_a_dynamic_control_takes_its_options_from_the_form_field_source(ws, c):
    dynamic = {"version": 1, "mode": "choice", "dynamic": True, "display": "select", "items": []}
    make(c, "pays", dynamic)
    publish(c, "pays")
    make(c, "fixe", CONTROL_EXAMPLES["choice"])
    publish(c, "fixe")
    rev = new_form(c)["revision"]
    source = {
        "connection": "annuaire",
        "path": "/pays",
        "items_path": "items",
        "value_path": "code",
        "label_path": "nom",
    }
    ok = call(
        c, "add_control_to_form", {"form": "cmd", "revision": rev, "control": "pays", "field": {"source": source}}
    )
    assert not ok[0]["isError"], ok[0]
    assert Form.objects.get(slug="cmd").draft_definition["sections"][0]["fields"][-1]["source"] == source
    refused = call(
        c,
        "add_control_to_form",
        {"form": "cmd", "revision": ok[1]["revision"], "control": "fixe", "field": {"source": source}},
    )[0]
    assert "ne reçoit pas ses options" in error_text(refused)


def test_conflicts_templates_and_unknowns(ws, c):
    make(c)
    rev = new_form(c)["revision"]
    stale = call(c, "add_control_to_form", {"form": "cmd", "revision": rev + 5, "control": "adresse"})[0]
    assert "modifié ailleurs" in error_text(stale)
    assert "Introuvable" in error_text(
        call(c, "add_control_to_form", {"form": "cmd", "revision": rev, "control": "absent"})[0]
    )
    assert "Introuvable" in error_text(
        call(c, "add_control_to_form", {"form": "absent", "revision": 1, "control": "adresse"})[0]
    )
    Form.objects.filter(slug="cmd").update(is_template=True)
    assert "modèle" in error_text(
        call(c, "add_control_to_form", {"form": "cmd", "revision": rev, "control": "adresse"})[0]
    )
    assert Form.objects.get(slug="cmd").draft_revision == rev


# --- formulaire : accès complet (style, CSS, grille, calculs, contrôles) -----------------------------------------------------------------------
def test_a_form_with_style_css_grid_compute_and_a_custom_control_is_created_and_the_css_is_normalized(ws, c):
    definition = {**FORM_FULL_EXAMPLE, "css": ".fb-field   {margin-bottom:8px}  /* commentaire */"}
    res, data = call(c, "create_form", {"slug": "complet", "name": "Complet", "definition": definition})
    assert not res["isError"], res
    stored = Form.objects.get(slug="complet").draft_definition
    assert (
        stored["css"] == formcss.normalize(".fb-field { margin-bottom: 8px; }")[0]
        and "commentaire" not in stored["css"]
    )
    assert stored["style"]["accent"] == "#2563eb" and stored["sections"][0]["fields"][1]["type"] == "custom"
    again = call(
        c,
        "update_form",
        {
            "slug": "complet",
            "revision": data["revision"],
            "definition": {**stored, "style": {**stored["style"], "density": "compact"}},
        },
    )
    assert (
        not again[0]["isError"] and Form.objects.get(slug="complet").draft_definition["style"]["density"] == "compact"
    )


def test_a_refused_style_or_css_is_explained(ws, c):
    pale = {**FORM_FULL_EXAMPLE, "style": {"accent": "#ffffcc"}}
    assert "style" in error_text(call(c, "create_form", {"slug": "pale", "name": "P", "definition": pale})[0]).lower()
    for css in (
        "& .x { background: url(http://evil.test/a.png); }",
        ".x { position: fixed; }",
        "@import 'x.css';",
        ".x { color: red !important; }",
    ):
        res = call(c, "create_form", {"slug": "mauvais", "name": "M", "definition": {**FORM_FULL_EXAMPLE, "css": css}})[
            0
        ]
        assert "css" in error_text(res).lower(), css
    assert not Form.objects.filter(slug__in=["pale", "mauvais"]).exists()


def test_validate_form_checks_everything_without_writing_and_against_a_workflow(ws, c):
    ok = call(c, "validate_form", {"definition": FORM_FULL_EXAMPLE})[1]
    assert ok == {"valid": True, "problems": {}, "against_workflow": {}}
    bad = call(c, "validate_form", {"definition": {**FORM_FULL_EXAMPLE, "css": ".x { z-index: 9; }"}})[1]
    assert bad["valid"] is False and "css" in bad["problems"] and "Ligne" in bad["problems"]["css"]
    broken = {
        "version": 1,
        "sections": [{"id": "s", "title": "T", "fields": [{"key": "a", "type": "inconnu", "label": "A"}]}],
    }
    assert call(c, "validate_form", {"definition": broken})[1]["valid"] is False
    Workflow.objects.create(
        workspace=ws,
        slug="wf",
        name="WF",
        draft_graph={
            "inputs": schema(["client", "montant"], client={"type": "string"}, montant={"type": "number"}),
            "nodes": [node("e", action="echo", inputs={"value": "x"})],
            "edges": [],
            "outputs": {},
        },
    )
    linked = call(c, "validate_form", {"definition": FORM_FULL_EXAMPLE, "workflow": "wf"})[1]
    assert (
        linked["valid"] is False and linked["against_workflow"]
    )  # « montant » (obligatoire) n'est demandé par aucun champ
    assert "Introuvable" in error_text(
        call(c, "validate_form", {"definition": FORM_FULL_EXAMPLE, "workflow": "absent"})[0]
    )
    assert not Form.objects.exists()


def test_dynamic_lists_are_reachable_through_update_form(ws, c):
    source = {
        "connection": "annuaire",
        "path": "/groupes",
        "items_path": "items",
        "value_path": "id",
        "label_path": "nom",
    }
    definition = {
        "version": 1,
        "sections": [
            {
                "id": "s",
                "title": "T",
                "fields": [{"key": "groupe", "type": "select", "label": "Groupe", "source": source}],
            }
        ],
    }
    res, _ = call(c, "create_form", {"slug": "liste", "name": "Liste", "definition": definition})
    assert not res["isError"], res
    assert Form.objects.get(slug="liste").draft_definition["sections"][0]["fields"][0]["source"] == source
