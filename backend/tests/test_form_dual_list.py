"""Liste double : un affichage des cases multiples (champ natif) et des contrôles « liste à cocher », où l'on met en ordre ce qu'on a choisi."""

import pytest

from tests.form_helpers import definition, field
from tests.test_form_controls import BOXES, BOXES_DETAIL, part
from triggers import formcontrols, formengine

OPTIONS = [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Bravo"}, {"value": "c", "label": "Charlie"}]


def custom(spec, key="k", **extra):
    return field(key, "custom", control={"ref": "boites", "version": "1.0.0", "spec": spec}, **extra)


# --- champ natif « Cases multiples » ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "display,ok", [("dual", True), ("list", True), ("grid", False), ("", False), (None, False), (1, False)]
)
def test_a_checkbox_list_may_be_shown_as_a_dual_list(display, ok):
    d = definition(field("m", "checkbox_list", options=OPTIONS, display=display))
    assert (formengine.check_definition(d) == {}) is ok


@pytest.mark.parametrize("kind", ["select", "radio", "combobox", "text", "checkbox"])
def test_only_the_checkbox_list_has_a_display(kind):
    extra = {"options": OPTIONS} if kind in ("select", "radio", "combobox") else {}
    assert formengine.check_definition(definition(field("m", kind, display="dual", **extra)))


def test_a_dual_list_may_read_its_options_from_a_service():
    src = {"connection": "svc", "path": "/x", "items_path": "groups"}
    assert formengine.check_definition(definition(field("m", "checkbox_list", source=src, display="dual"))) == {}


def test_the_order_chosen_in_a_dual_list_is_the_value_the_workflow_gets():
    d = definition(field("m", "checkbox_list", options=OPTIONS, display="dual"))
    kept, problems = formengine.validate_submission(d, {"m": ["c", "a", "b"]})
    assert problems == {} and kept["m"] == ["c", "a", "b"]  # ni remis dans l'ordre des options, ni dédoublé
    assert formengine.validate_submission(d, {"m": ["a", "zzz"]})[1]  # un choix non proposé reste refusé


# --- contrôle personnalisé « liste à cocher » ------------------------------------------------------------------------------------
def test_a_boxes_control_may_be_shown_as_a_dual_list_when_it_has_no_details():
    assert formcontrols.check_spec({**BOXES, "display": "dual"}) == {}
    assert formengine.check_definition(definition(custom({**BOXES, "display": "dual"}))) == {}


def test_a_dual_list_has_no_details_per_option():
    bad = formcontrols.check_spec({**BOXES_DETAIL, "display": "dual"})
    assert "display" in " ".join(bad) and "détails" in " ".join(bad.values())
    on_item = {
        **BOXES,
        "display": "dual",
        "items": [{**BOXES["items"][0], "parts": [part("n", "number")]}, *BOXES["items"][1:]],
    }
    assert formcontrols.check_spec(on_item)


def test_a_choice_control_has_no_dual_display():
    choice = {"version": 1, "mode": "choice", "items": BOXES["items"], "display": "dual"}
    assert formcontrols.check_spec(choice)


def test_a_boxes_control_keeps_the_order_sent_only_in_dual_mode():
    plain = custom({**BOXES, "max": 3, "min": 0})
    dual = custom({**BOXES, "max": 3, "min": 0, "display": "dual"})
    assert formcontrols.process(plain, ["c", "a"])[0] == ["a", "c"]  # comportement existant : l'ordre des options
    kept, problem = formcontrols.process(dual, ["c", "a", "b"])
    assert not problem
    assert kept == ["c", "a", "b"]  # l'ordre choisi est la valeur


def test_a_dual_boxes_control_still_refuses_duplicates_unknown_and_disabled_choices():
    spec = {
        **BOXES,
        "max": 3,
        "min": 0,
        "display": "dual",
        "items": [*BOXES["items"][:2], {"value": "c", "label": "Charlie", "disabled": True}],
    }
    field_ = custom(spec)
    assert formcontrols.process(field_, ["a", "a"])[1]
    assert formcontrols.process(field_, ["zzz"])[1]
    assert formcontrols.process(field_, ["c"])[1]
    kept, problem = formcontrols.process(field_, ["b", "a"])
    assert not problem and kept == ["b", "a"]


# --- titres des deux colonnes -----------------------------------------------------------------------------------------------------
TITLES = {"available_label": "Membres du groupe", "chosen_label": "À retirer"}


def test_the_two_columns_of_a_dual_list_can_be_renamed():
    d = definition(field("m", "checkbox_list", options=OPTIONS, display="dual", **TITLES))
    assert formengine.check_definition(d) == {}
    only_one = definition(field("m", "checkbox_list", options=OPTIONS, display="dual", chosen_label="À retirer"))
    assert formengine.check_definition(only_one) == {}


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61, 3, None, ["a"]])
def test_a_column_title_is_a_short_non_empty_text(bad):
    d = definition(field("m", "checkbox_list", options=OPTIONS, display="dual", available_label=bad))
    assert formengine.check_definition(d)


def test_column_titles_only_exist_for_the_dual_list():
    assert formengine.check_definition(
        definition(field("m", "checkbox_list", options=OPTIONS, **TITLES))
    )  # cases à cocher
    assert formengine.check_definition(
        definition(field("m", "checkbox_list", options=OPTIONS, display="list", **TITLES))
    )
    assert formengine.check_definition(definition(field("m", "select", options=OPTIONS, **TITLES)))


def test_the_columns_of_a_dual_boxes_control_can_be_renamed_too():
    assert formcontrols.check_spec({**BOXES, "display": "dual", **TITLES}) == {}
    assert formcontrols.check_spec({**BOXES, **TITLES})  # pas en liste double
    assert formcontrols.check_spec({**BOXES, "display": "dropdown", **TITLES})
    assert formcontrols.check_spec({"version": 1, "mode": "choice", "items": BOXES["items"], **TITLES})
    assert formcontrols.check_spec({**BOXES, "display": "dual", "chosen_label": "x" * 61})
    assert formengine.check_definition(definition(custom({**BOXES, "display": "dual", **TITLES}))) == {}
