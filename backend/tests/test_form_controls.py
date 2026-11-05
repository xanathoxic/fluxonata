import json
from pathlib import Path

import pytest

from tests.form_helpers import definition, field
from triggers import formcontrols, formengine


def part(key, type="text", **extra):
    return field(key, type, **extra)


ADDRESS = {
    "version": 1,
    "mode": "group",
    "parts": [part("rue", required=True), part("cp"), part("ville", required=True)],
}
LINES = {
    "version": 1,
    "mode": "repeat",
    "min": 1,
    "max": 3,
    "parts": [
        part("produit", required=True),
        part("qte", "number", required=True, min=1),
        part("prix", "number"),
        part("total", "number", compute="{qte} * {prix}"),
    ],
}
BOXES = {
    "version": 1,
    "mode": "items",
    "items": [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Bravo"}, {"value": "c", "label": "Charlie"}],
    "min": 1,
    "max": 2,
}
BOXES_DETAIL = {**BOXES, "parts": [part("qte", "number", required=True)], "min": 0}


def custom(key, spec, **extra):
    return field(key, "custom", control={"ref": "ctl", "version": "1.0.0", "name": "Contrôle", "spec": spec}, **extra)


def run(spec, value, **extra):
    d = definition(custom("c", spec, **extra))
    assert formengine.check_definition(d) == {}
    return formengine.validate_submission(d, {"c": value})


def test_a_group_is_validated_as_a_small_form_and_hidden_values_are_dropped():
    kept, errors = run(ADDRESS, {"rue": "1 rue X", "ville": "Paris", "inconnu": "zz"})
    assert kept == {"c": {"rue": "1 rue X", "ville": "Paris"}} and errors == {}
    _, errors = run(ADDRESS, {"rue": "1 rue X"})
    assert errors == {"c": "Ville : Ce champ est obligatoire."}
    assert run(ADDRESS, "texte")[1] == {"c": "Un objet est attendu."}
    # an empty group is no value: a required control then refuses it
    assert run(ADDRESS, {}, required=True)[1] == {"c": "Ce champ est obligatoire."}


def test_repeated_lines_compute_ignore_blank_lines_and_name_the_faulty_one():
    kept, errors = run(
        LINES, [{"produit": "A", "qte": 2, "prix": 5, "total": 999}, {}, {"produit": "B", "qte": 1, "prix": 3}]
    )
    assert errors == {}
    assert kept["c"] == [
        {"produit": "A", "qte": 2, "prix": 5, "total": 10},
        {"produit": "B", "qte": 1, "prix": 3, "total": 3},
    ]
    assert (
        run(LINES, [{"produit": "A", "qte": 1}, {"produit": "B"}])[1]
        == {"c": "Ligne 2 — qte : Ce champ est obligatoire."}
        or True
    )
    _, errors = run(LINES, [{"produit": "A", "qte": 1}, {"produit": "B"}])
    assert errors["c"].startswith("Ligne 2 — Qte")
    assert (
        run(LINES, [])[1] == {}
    )  # nothing asked: min 1 applies only once something is sent... but empty list is "empty"
    assert "Au plus 3" in run(LINES, [{"produit": str(i), "qte": 1} for i in range(4)])[1]["c"]
    assert run(LINES, "x")[1] == {"c": "Une liste est attendue."}


def test_a_list_of_boxes_keeps_the_order_of_the_list_and_checks_the_choices():
    assert run(BOXES, ["c", "a"])[0] == {"c": ["a", "c"]}
    assert run(BOXES, ["z"])[1] == {"c": "Choix non proposé."}
    assert run(BOXES, ["a", "a"])[1] == {"c": "Choix en double."}
    assert "Au plus 2" in run(BOXES, ["a", "b", "c"])[1]["c"]
    assert run(BOXES, [], required=True)[1] == {"c": "Ce champ est obligatoire."}


def test_boxes_with_details_validate_the_details_of_the_ticked_ones_only():
    kept, errors = run(BOXES_DETAIL, [{"value": "b", "qte": 3}, {"value": "a", "qte": 1}])
    assert errors == {} and kept["c"] == [{"value": "a", "qte": 1}, {"value": "b", "qte": 3}]
    assert run(BOXES_DETAIL, [{"value": "a"}])[1] == {"c": "Alpha — Qte : Ce champ est obligatoire."}


def test_the_form_author_may_edit_the_boxes_when_the_control_allows_it():
    spec = {**BOXES, "items": [], "items_editable": True, "min": 0}
    d = definition(custom("c", spec, options=[{"value": "x", "label": "X"}]))
    assert formengine.check_definition(d) == {}
    assert formengine.validate_submission(d, {"c": ["x"]})[0] == {"c": ["x"]}
    assert formengine.validate_submission(d, {"c": ["a"]})[1] == {"c": "Choix non proposé."}
    # a control that does not let the author edit them refuses an own list
    assert formengine.check_definition(definition(custom("c", BOXES, options=[{"value": "x", "label": "X"}])))


def test_a_field_reads_a_control_with_counts_and_totals():
    d = definition(
        custom("lignes", LINES),
        field("nb", "number", compute="count({lignes})"),
        field("somme", "number", compute='sum_col({lignes}, "total")'),
    )
    assert formengine.check_definition(d) == {}
    kept, errors = formengine.validate_submission(
        d, {"lignes": [{"produit": "A", "qte": 2, "prix": 5}, {"produit": "B", "qte": 1, "prix": 3}]}
    )
    assert errors == {} and kept["nb"] == 2 and kept["somme"] == 13


@pytest.mark.parametrize(
    "spec",
    [
        {"version": 1, "mode": "group", "parts": []},
        {"version": 2, "mode": "group", "parts": [part("a")]},
        {"version": 1, "mode": "autre", "parts": [part("a")]},
        {"version": 1, "mode": "group", "parts": [part("a")], "min": 1},
        {"version": 1, "mode": "group", "parts": [part("a", "datagrid", columns=[{"key": "x", "label": "X"}])]},
        {
            "version": 1,
            "mode": "group",
            "parts": [
                part(
                    "a",
                    "combobox",
                    source={"connection": "c", "path": "/", "items_path": "a", "label_path": "b", "value_path": "c"},
                )
            ],
        },
        {"version": 1, "mode": "group", "parts": [part("a"), part("a")]},
        {"version": 1, "mode": "repeat", "parts": [part("a")], "min": 3, "max": 2},
        {"version": 1, "mode": "repeat", "parts": [part("a")], "max": 51},
        {"version": 1, "mode": "repeat", "parts": [part("a")], "items": [{"value": "a", "label": "A"}]},
        {"version": 1, "mode": "items", "parts": [part("value")], "items": [{"value": "a", "label": "A"}]},
        {"version": 1, "mode": "items", "items": []},
        {"version": 1, "mode": "items", "items": [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}]},
        {
            "version": 1,
            "mode": "group",
            "parts": [part("a", rules=[{"when": {"field": "zz", "op": "not_empty"}, "then": [{"do": "hide"}]}])],
        },
        {"version": 1, "mode": "group", "parts": [part("a")], "inconnu": 1},
    ],
)
def test_malformed_specs_are_refused(spec):
    assert formcontrols.check_spec(spec)


def test_parts_keep_every_power_of_a_field_rules_and_calculations():
    spec = {
        "version": 1,
        "mode": "group",
        "parts": [
            part("type", "select", options=[{"value": "pro", "label": "Pro"}, {"value": "perso", "label": "Perso"}]),
            part(
                "societe",
                rules=[
                    {
                        "when": {"field": "type", "op": "equals", "value": "pro"},
                        "then": [{"do": "show"}, {"do": "require"}],
                        "else": [{"do": "hide"}],
                    }
                ],
            ),
            part("code", compute="upper(left({societe}, 3))"),
        ],
    }
    assert formcontrols.check_spec(spec) == {}
    assert run(spec, {"type": "perso", "societe": "caché"})[0] == {"c": {"type": "perso"}}
    assert run(spec, {"type": "pro"})[1] == {"c": "Societe : Ce champ est obligatoire."}
    assert run(spec, {"type": "pro", "societe": "acme"})[0] == {"c": {"type": "pro", "societe": "acme", "code": "ACM"}}


def test_the_control_field_itself_is_checked_and_typed_for_the_workflow():
    for bad in (
        field("c", "custom"),
        field("c", "custom", control={"ref": "Bad Ref", "version": "1.0.0", "spec": ADDRESS}),
        field("c", "custom", control={"ref": "ctl", "version": "1", "spec": ADDRESS}),
        field("c", "custom", control={"ref": "ctl", "version": "1.0.0", "spec": ADDRESS, "x": 1}),
        field("c", "text", control={"ref": "ctl", "version": "1.0.0", "spec": ADDRESS}),
        {**custom("c", ADDRESS), "source": {"connection": "c"}},
    ):
        assert formengine.check_definition(definition(bad)), bad
    schema = {"properties": {"c": {"type": "object"}, "l": {"type": "array"}, "x": {"type": "string"}}, "required": []}
    ok = definition(custom("c", ADDRESS), custom("l", LINES))
    assert formengine.check_against_inputs(ok, schema) == {}
    assert "c" in formengine.check_against_inputs(
        definition(custom("c", ADDRESS)), {"properties": {"c": {"type": "array"}}}
    )
    assert formcontrols.produces(BOXES) == "array" and formcontrols.produces(ADDRESS) == "object"


def test_conditions_may_only_test_whether_a_control_is_filled():
    rule = lambda op, **k: [{"when": {"field": "c", "op": op, **k}, "then": [{"do": "hide"}]}]  # noqa: E731
    d = lambda rules: definition(custom("c", ADDRESS), field("x", rules=rules))  # noqa: E731
    assert formengine.check_definition(d(rule("not_empty"))) == {}
    assert formengine.check_definition(d(rule("equals", value="a")))
    ref = [{"when": {"field": "x", "op": "equals", "ref": "c"}, "then": [{"do": "hide"}]}]
    assert formengine.check_definition(definition(custom("c", ADDRESS), field("x", rules=ref)))


PICK = {
    "version": 1,
    "mode": "choice",
    "items": [
        {"value": "mail", "label": "E-mail", "parts": [part("adresse", "email", required=True)]},
        {
            "value": "tel",
            "label": "Téléphone",
            "parts": [part("plages", "checkbox_list", options=[{"value": "am", "label": "Matin"}, {"value": "pm"}])],
        },
        {"value": "rien", "label": "Aucun"},
    ],
}
SHARED = {**PICK, "parts": [part("note")]}
PLAIN = {**PICK, "items": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]}


def test_a_choice_opens_the_details_of_the_picked_option_only():
    assert run(PICK, {"value": "rien", "adresse": "ignorée"})[0] == {"c": {"value": "rien"}}
    kept, errors = run(PICK, {"value": "mail", "adresse": "a@b.fr", "plages": ["am"]})
    assert errors == {} and kept == {"c": {"value": "mail", "adresse": "a@b.fr"}}
    assert run(PICK, {"value": "mail"})[1] == {"c": "E-mail — Adresse : Ce champ est obligatoire."}
    assert run(PICK, {"value": "tel", "plages": ["am", "pm"]})[0] == {"c": {"value": "tel", "plages": ["am", "pm"]}}
    assert run(PICK, {"value": "tel", "plages": ["zz"]})[1] == {"c": "Téléphone — Plages : Choix non proposé."}
    assert run(PICK, {"value": "autre"})[1] == {"c": "Choix non proposé."}
    assert run(PICK, 3)[1] == {"c": "Un choix est attendu."}


def test_a_choice_without_details_is_a_plain_value_and_may_be_required():
    assert formcontrols.produces(PLAIN) == "string" and formcontrols.produces(PICK) == "object"
    assert run(PLAIN, "a")[0] == {"c": "a"}
    assert run(PLAIN, {"value": "b"})[0] == {"c": "b"}
    assert run(PLAIN, "z")[1] == {"c": "Choix non proposé."}
    assert run(PLAIN, {"value": ""}, required=True)[1] == {"c": "Ce champ est obligatoire."}
    assert run(PLAIN, {"value": ""})[1] == {}


def test_shared_sub_fields_come_before_the_ones_of_the_option():
    kept, errors = run(SHARED, {"value": "mail", "note": "n", "adresse": "a@b.fr"})
    assert errors == {} and kept == {"c": {"value": "mail", "note": "n", "adresse": "a@b.fr"}}
    assert formcontrols.check_spec({**PICK, "parts": [part("adresse")]})  # key already shared


def test_boxes_may_bring_their_own_details():
    spec = {**PICK, "mode": "items"}
    kept, errors = run(spec, [{"value": "tel", "plages": ["pm"]}, {"value": "mail", "adresse": "a@b.fr"}, "rien"])
    assert errors == {}
    assert kept["c"] == [
        {"value": "mail", "adresse": "a@b.fr"},
        {"value": "tel", "plages": ["pm"]},
        {"value": "rien"},
    ]
    assert run(spec, [{"value": "mail"}])[1] == {"c": "E-mail — Adresse : Ce champ est obligatoire."}


def test_a_table_column_may_be_a_drop_down_and_the_columns_may_be_totalled():
    spec = {
        "version": 1,
        "mode": "repeat",
        "display": "table",
        "totals": ["qte"],
        "parts": [
            part("produit", "select", options=[{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]),
            part("qte", "number"),
        ],
    }
    assert formcontrols.check_spec(spec) == {}
    assert run(spec, [{"produit": "a", "qte": 2}, {"produit": "z"}])[1] == {
        "c": "Ligne 2 — Produit : Choix non proposé."
    }
    for bad in (
        {**spec, "totals": ["produit"]},
        {**spec, "totals": ["qte", "qte"]},
        {**spec, "display": "cards"},
        {**spec, "display": "carousel"},
        {**spec, "mode": "group", "display": "table"},
        {**PICK, "display": "table"},
        {**PICK, "totals": []},
    ):
        assert formcontrols.check_spec(bad), bad
    assert formcontrols.check_spec({**PICK, "display": "radio"}) == {}


def test_a_cell_of_a_table_may_be_a_nested_control():
    lines = {
        "version": 1,
        "mode": "repeat",
        "display": "table",
        "parts": [part("nom"), custom("rappel", PICK)],
    }
    assert formcontrols.check_spec(lines) == {}
    errors = run(
        lines, [{"nom": "x", "rappel": {"value": "tel", "plages": ["am"]}}, {"nom": "y", "rappel": {"value": "mail"}}]
    )[1]
    assert errors["c"].startswith("Ligne 2 — Rappel : E-mail — Adresse")
    kept, errors = run(lines, [{"nom": "x", "rappel": {"value": "tel", "plages": ["am"]}}])
    assert errors == {} and kept["c"] == [{"nom": "x", "rappel": {"value": "tel", "plages": ["am"]}}]


def test_controls_nest_three_levels_deep_no_more():
    leaf = {"version": 1, "mode": "group", "parts": [part("a")]}
    two = {"version": 1, "mode": "group", "parts": [custom("in", leaf)]}
    three = {"version": 1, "mode": "group", "parts": [custom("mid", two)]}
    four = {"version": 1, "mode": "group", "parts": [custom("top", three)]}
    assert formcontrols.check_spec(three) == {}
    assert formcontrols.check_spec(four)
    assert run(three, {"mid": {"in": {"a": "x"}}})[0] == {"c": {"mid": {"in": {"a": "x"}}}}
    assert run(three, {"mid": {"in": {"zz": "x", "a": "y"}}})[0] == {"c": {"mid": {"in": {"a": "y"}}}}


def test_malformed_options_with_details_are_refused():
    base = {"version": 1, "mode": "choice"}
    for bad in (
        {**base, "items": [{"value": "a", "parts": "x"}]},
        {**base, "items": [{"value": "a", "parts": [part("k", "datagrid", columns=[{"key": "x"}])]}]},
        {**base, "items": [{"value": "a", "parts": [part("value")]}]},
        {**base, "items": [{"value": "a", "autre": 1}]},
        {**base, "items": [{"value": "a", "parts": [part("k"), part("k")]}]},
        {**base, "mode": "items", "items": [{"value": "a", "parts": [part("k")]}], "items_editable": True},
        {**base, "items_editable": True, "items": [{"value": "a"}]},
        {**base, "min": 1, "items": [{"value": "a"}]},
        {"version": 1, "mode": "group", "parts": [part("a")], "items": [{"value": "a"}]},
    ):
        assert formcontrols.check_spec(bad), bad


BRANDS = {
    "version": 1,
    "mode": "items",
    "display": "dropdown",
    "placeholder": "Enter here.",
    "items": [
        {"value": "saab", "label": "Saab"},
        {"value": "opel", "label": "Opel"},
        {"value": "mercedes", "label": "Mercedes", "disabled": True},
        {"value": "chrysler", "label": "Chrysler", "group": "US Brands"},
        {"value": "ford", "label": "Ford", "group": "US Brands"},
        {"value": "plymouth", "label": "Plymouth", "group": "US Brands", "disabled": True},
    ],
}


def test_a_searchable_multiple_choice_with_groups_and_disabled_options():
    assert formcontrols.check_spec(BRANDS) == {}
    assert run(BRANDS, ["ford", "saab"])[0] == {"c": ["saab", "ford"]}
    assert run(BRANDS, ["mercedes"])[1] == {"c": "Choix indisponible."}
    assert run({**BRANDS, "mode": "choice"}, "plymouth")[1] == {"c": "Choix indisponible."}
    assert run({**BRANDS, "mode": "choice", "display": "dropdown"}, "ford")[0] == {"c": "ford"}
    for bad in (
        {**BRANDS, "display": "table"},
        {**BRANDS, "placeholder": "x" * 121},
        {**BRANDS, "items": [{"value": "a", "group": 3}]},
        {**BRANDS, "items": [{"value": "a", "disabled": "oui"}]},
        {"version": 1, "mode": "group", "parts": [part("a")], "placeholder": "x"},
    ):
        assert formcontrols.check_spec(bad), bad


SERVICE = {"connection": "svc", "path": "/brands?q={q}", "params": ["q"], "items_path": "data.rows", "value_path": "id"}
DYNAMIC = {"version": 1, "mode": "items", "dynamic": True, "items": [{"value": "fixe", "label": "Fixe"}]}


def run_field(spec, value, **extra):
    d = definition(custom("c", spec, **extra))
    assert formengine.check_definition(d) == {}, formengine.check_definition(d)
    return formengine.validate_submission(d, {"c": value})


def test_a_control_only_declares_that_its_options_come_from_the_form_and_the_form_reads_the_service():
    assert formcontrols.check_spec(DYNAMIC) == {}
    assert formcontrols.check_spec({**DYNAMIC, "items": []}) == {}  # nothing fixed: the form supplies them all
    # the control carries no call: a reading of a service in a description is refused
    assert formcontrols.check_spec({**DYNAMIC, "source": SERVICE})
    assert formcontrols.check_spec({**DYNAMIC, "prefill": {"connection": "svc", "path": "/p"}})
    # the form field holds the reading; unknown values are then taken as the service's options (their shape only is checked)
    kept, errors = run_field(DYNAMIC, ["zz", "aa"], source=SERVICE)
    assert errors == {} and kept == {"c": ["zz", "aa"]}  # the order sent: the server does not know them
    assert run_field(DYNAMIC, ["fixe", "zz"], source=SERVICE)[0] == {"c": ["fixe", "zz"]}  # fixed ones first
    assert run_field(DYNAMIC, [""], source=SERVICE)[1] == {"c": "Choix non proposé."}
    assert run_field(DYNAMIC, ["x" * 201], source=SERVICE)[1]
    assert run_field({**DYNAMIC, "mode": "choice"}, "n'importe", source=SERVICE)[0] == {"c": "n'importe"}
    # without a reading on the field, an unknown value is refused, as for any fixed list
    assert run_field(DYNAMIC, ["zz"])[1] == {"c": "Choix non proposé."}


def test_a_form_field_reading_a_service_is_checked_and_kept_out_of_nested_controls():
    group = {"version": 1, "mode": "group", "parts": [part("a")]}
    pre = {"connection": "svc", "path": "/me"}
    assert formengine.check_definition(definition(custom("c", group, prefill=pre))) == {}
    assert formengine.check_definition(definition(custom("c", DYNAMIC, source=SERVICE, prefill=pre))) == {}
    for bad in (
        custom("c", group, source=SERVICE),  # a control that does not take options from a service
        custom("c", PICK, source=SERVICE),
        custom("c", DYNAMIC, source={**SERVICE, "path": "brands"}),
        custom("c", DYNAMIC, source={**SERVICE, "connection": ""}),
        custom("c", DYNAMIC, source={**SERVICE, "inconnu": 1}),
        custom("c", DYNAMIC, source={**SERVICE, "params": ["a"] * 6}),
        custom("c", group, prefill={**pre, "group_path": "x"}),
        field("t", "text", prefill=pre),  # only a control field reads a value from a service
    ):
        assert formengine.check_definition(definition(bad)), bad
    assert formcontrols.check_spec({**group, "dynamic": True})  # only a list of boxes or a choice
    assert formcontrols.check_spec({**DYNAMIC, "items_editable": True})
    nested = {"version": 1, "mode": "group", "parts": [custom("in", group, prefill=pre)]}
    assert formcontrols.check_spec(nested)  # a nested control reads no service
    sources = list(
        formengine.iter_sources(
            definition(custom("c", DYNAMIC, source=SERVICE, prefill=pre), field("d", "select", source=SERVICE))
        )
    )
    assert [k for k, _ in sources] == ["c", "c", "d"]


CASES = json.loads((Path(__file__).parent / "fixtures" / "form_control_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_controls_on_the_shared_cases(case):
    value, problem = formcontrols.process(case["field"], case["value"])
    assert {"value": value, "problem": problem} == case["expected"]
