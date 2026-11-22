"""Conditions du bouton d'envoi : mêmes règles que celles d'un champ, lues sur les champs de données ; le serveur refuse un envoi
dont le bouton serait masqué ou bloqué (un envoi forgé hors de l'interface ne les contourne pas)."""

from tests.form_helpers import definition, field
from triggers import formengine

LOCK = [
    {
        "when": {"field": "ok", "op": "is_true"},
        "then": [{"do": "editable"}],
        "else": [{"do": "readonly"}, {"do": "text", "target": "help", "text": "Cochez la confirmation."}],
    }
]


def form(rules, *extra):
    return {**definition(field("nom"), field("ok", "checkbox"), *extra), "submit": {"rules": rules}}


def test_rules_on_the_submit_button_are_accepted():
    assert formengine.check_definition(form(LOCK)) == {}
    hide = [{"when": {"field": "nom", "op": "is_empty"}, "then": [{"do": "hide"}]}]
    label = [
        {
            "when": {"field": "nom", "op": "equals", "value": "x"},
            "then": [{"do": "text", "target": "label", "text": "Go {nom}"}],
        }
    ]
    assert formengine.check_definition(form(hide)) == {} and formengine.check_definition(form(label)) == {}


def test_a_form_without_submit_conditions_is_unchanged():
    assert formengine.check_definition(definition(field("nom"))) == {}
    assert formengine.check_definition({**definition(field("nom")), "submit": {}}) == {}
    assert formengine.check_definition({**definition(field("nom")), "submit": {"rules": []}}) == {}


def test_the_submit_button_refuses_what_has_no_meaning_for_a_button():
    when = {"field": "ok", "op": "is_true"}
    for effect in (
        {"do": "require"},
        {"do": "optional"},
        {"do": "fill", "value": "x"},
        {"do": "text", "target": "placeholder", "text": "x"},
        {"do": "options", "values": ["a"]},
        {"do": "load", "what": "options"},
    ):
        assert formengine.check_definition(form([{"when": when, "then": [effect]}])), effect


def test_submit_conditions_read_existing_fields_only_and_nothing_else_is_allowed_on_submit():
    unknown = [{"when": {"field": "absent", "op": "is_true"}, "then": [{"do": "hide"}]}]
    assert formengine.check_definition(form(unknown))
    assert formengine.check_definition({**definition(field("nom")), "submit": {"label": "Go"}})
    assert formengine.check_definition({**definition(field("nom")), "submit": "oui"})


def test_the_server_refuses_a_submission_when_the_button_is_blocked():
    d = form(LOCK)
    values, errors = formengine.validate_submission(d, {"nom": "a", "ok": False})
    assert errors == {"_": "Cochez la confirmation."}
    assert values["nom"] == "a"
    assert formengine.validate_submission(d, {"nom": "a", "ok": True})[1] == {}


def test_a_blocked_button_without_a_message_gets_a_default_one():
    d = form([{"when": {"field": "ok", "op": "is_false"}, "then": [{"do": "readonly"}]}])
    errors = formengine.validate_submission(d, {"nom": "a", "ok": False})[1]
    assert "condition" in errors["_"]


def test_a_hidden_button_means_no_submission():
    d = form([{"when": {"field": "nom", "op": "is_empty"}, "then": [{"do": "hide"}]}])
    assert "_" in formengine.validate_submission(d, {"ok": True})[1]
    assert formengine.validate_submission(d, {"nom": "a", "ok": True})[1] == {}


def test_conditions_read_the_values_the_form_keeps_not_a_hidden_field():
    hidden = form(
        [{"when": {"field": "nom", "op": "equals", "value": "secret"}, "then": [{"do": "readonly"}]}],
    )
    hidden["sections"][0]["fields"][0]["rules"] = [{"when": {"field": "ok", "op": "is_true"}, "then": [{"do": "hide"}]}]
    # « nom » est masqué quand la case est cochée : sa valeur envoyée est écartée, la condition ne la voit pas
    assert formengine.validate_submission(hidden, {"nom": "secret", "ok": True})[1] == {}
    assert "_" in formengine.validate_submission(hidden, {"nom": "secret", "ok": False})[1]
