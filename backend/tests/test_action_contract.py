"""The contract of an action: what it does, every input and output described (or said to be absent)."""

from catalog import contract
from catalog.demo_actions import DEMO_ACTIONS
from catalog.models import Action
from tests.conftest import client_for

BASE = "/api/v1/workspaces/team-a"
GOOD_IN = {
    "type": "object",
    "required": ["n"],
    "properties": {"n": {"type": "integer", "title": "Nombre", "description": "Le nombre à doubler."}},
}
GOOD_OUT = {
    "type": "object",
    "properties": {"double": {"type": "integer", "title": "Double", "description": "Le nombre multiplié par deux."}},
}
SRC = "def run(inputs):\n    return {'double': inputs['n'] * 2}\n"
SUMMARY = "Double un nombre entier et renvoie le résultat, sans rien modifier d'autre."


def body(**version):
    return {
        "slug": "doubler",
        "name": "Doubler",
        "description": SUMMARY,
        "version": {"source": SRC, "input_schema": GOOD_IN, "output_schema": GOOD_OUT, **version},
    }


def test_every_demo_action_has_a_complete_contract():
    for spec in DEMO_ACTIONS:
        assert contract.problems(spec["description"], spec["input_schema"], spec["output_schema"]) == [], spec["slug"]


def test_the_problems_say_exactly_what_to_add():
    miss = contract.problems(
        "Court", {"type": "object", "properties": {"x": {"type": "string"}}}, {"type": "object", "properties": {}}
    )
    assert any("Description de l'action" in m for m in miss)
    assert any("Entrée « x » : indiquez son type" not in m for m in miss)  # it has a type
    assert any("Entrée « x » : ajoutez un titre" in m for m in miss) and any(
        "Entrée « x » : ajoutez une description (à quoi sert" in m for m in miss
    )
    assert any("Sorties : cette action n'a pas de paramètre" in m for m in miss)  # no output must be SAID
    out_bad = contract.problems(SUMMARY, GOOD_IN, {"type": "object", "properties": {"y": {"title": "Y"}}})
    assert any("Sortie « y » : indiquez son type" in m for m in out_bad) and any(
        "Sortie « y » : ajoutez une description (ce que contient" in m for m in out_bad
    )


def test_no_input_and_no_output_are_stated_not_guessed():
    none_in = {"type": "object", "properties": {}, "description": "Cette action n'a aucune entrée."}
    none_out = {"type": "object", "properties": {}, "description": "Cette action n'a aucune sortie."}
    assert contract.problems(SUMMARY, none_in, none_out) == []
    c = contract.contract(SUMMARY, none_in, none_out, None)
    assert (
        c["inputs"] == []
        and c["inputs_note"] == "Cette action n'a aucune entrée."
        and c["outputs_note"] == "Cette action n'a aucune sortie."
    )
    assert c["complete"] and c["effects"]["side_effects"] == "none"


def test_the_contract_is_shown_field_by_field():
    schema = {
        "type": "object",
        "required": ["l"],
        "properties": {
            "l": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Liste",
                "description": "Des noms.",
                "maxItems": 5,
            },
            "k": {
                "type": ["integer", "null"],
                "title": "K",
                "description": "Un entier facultatif.",
                "minimum": 1,
                "default": 3,
            },
        },
    }
    rows = {r["name"]: r for r in contract.fields_of(schema)}
    assert rows["l"]["type"] == "liste de texte" and rows["l"]["required"] and rows["l"]["limits"] == "au plus 5"
    assert (
        rows["k"]["type"] == "entier (peut être vide)"
        and not rows["k"]["required"]
        and rows["k"]["default"] == 3
        and rows["k"]["limits"] == "minimum 1"
    )


def test_publishing_an_action_requires_the_complete_contract(world):
    c = client_for(world["designer"])
    bad = body(input_schema={"type": "object", "properties": {"n": {"type": "integer"}}})
    r = c.post(f"{BASE}/library/actions/", bad, format="json")
    assert r.status_code == 400 and any("Entrée « n »" in m for m in r.json()["error"]["details"]["contract"])
    short = {**body(), "description": "Double."}
    assert c.post(f"{BASE}/library/actions/", short, format="json").status_code == 400
    ok = c.post(f"{BASE}/library/actions/", body(), format="json")
    assert ok.status_code == 201 and ok.json()["contract_complete"] is True
    # a new version is held to the same rule
    v = c.post(
        f"{BASE}/actions/doubler/versions/",
        {
            "source": SRC,
            "input_schema": GOOD_IN,
            "output_schema": {"type": "object", "properties": {"d": {"type": "integer"}}},
        },
        format="json",
    )
    assert v.status_code == 400 and any("Sortie « d »" in m for m in v.json()["error"]["details"]["contract"])
    good = c.post(
        f"{BASE}/actions/doubler/versions/",
        {"source": SRC, "input_schema": GOOD_IN, "output_schema": GOOD_OUT},
        format="json",
    )
    assert (
        good.status_code == 201
        and good.json()["contract"]["complete"]
        and good.json()["contract"]["outputs"][0]["name"] == "double"
    )


def test_an_older_incomplete_version_is_reported_not_hidden(world):
    from catalog.models import ActionVersion

    a = Action.objects.create(workspace=world["ws_a"], slug="vieille", name="Vieille", description="")
    ActionVersion.objects.create(
        action=a, version="1.0.0", source=SRC, input_schema={"type": "object", "properties": {}}, output_schema=GOOD_OUT
    )
    got = client_for(world["designer"]).get(f"{BASE}/actions/vieille/").json()
    assert got["contract_complete"] is False
    c = got["latest_version"]["contract"]
    assert (
        not c["complete"]
        and any("Description de l'action" in m for m in c["problems"])
        and c["inputs_note"] == "Aucune entrée."
    )
