"""Caractérisation de `triggers.formengine.check_definition` : ordre des refus (structure, puis champs, puis règles, puis boucles) et clés des
problèmes relevés. Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après."""

import pytest

from tests.form_helpers import definition, field
from triggers import formengine
from triggers.formengine import MAX_FIELDS, MAX_SECTIONS, check_definition


def sections_of(*fields_per_section):
    return {
        "version": 1,
        "sections": [{"id": f"s{i}", "title": "T", "fields": list(fs)} for i, fs in enumerate(fields_per_section)],
    }


def test_a_well_formed_definition_has_no_problem():
    assert check_definition(definition(field("a"), field("b", "number"))) == {}


@pytest.mark.parametrize("bad", [None, [], "x", {"sections": [], "inconnu": 1}])
def test_a_definition_that_is_not_an_object_with_known_keys_is_refused_as_a_whole(bad):
    assert list(check_definition(bad)) == ["_"]


@pytest.mark.parametrize("ref", ["x", {"form": "a", "extra": 1}, {"form": "a" * 61}])
def test_a_bad_form_reference_stops_everything_else(ref):
    d = {**definition(field("a")), "ref": ref}
    assert list(check_definition(d)) == ["ref"]


def test_a_good_reference_is_accepted_and_a_null_one_is_ignored():
    assert check_definition({**definition(field("a")), "ref": {"form": "f", "version": "1.0.0"}}) == {}
    assert check_definition({**definition(field("a")), "ref": None}) == {}


def test_the_number_of_sections_is_bounded():
    assert list(check_definition({"sections": []})) == ["sections"]
    assert list(check_definition({"sections": "x"})) == ["sections"]
    many = {"sections": [{"id": f"s{i}", "title": "T", "fields": []} for i in range(MAX_SECTIONS + 1)]}
    assert list(check_definition(many)) == ["sections"]


def test_a_section_is_checked_on_its_own_and_the_others_still_are():
    d = {
        "sections": [
            "pas un objet",
            {"id": "s", "title": "x" * 161, "description": "y" * 2001, "fields": "x"},
            {"id": "t", "extra": 1, "fields": []},
        ]
    }
    problems = check_definition(d)
    assert problems["sections.0"] == "Section invalide."
    assert problems["sections.1.title"].startswith("Titre trop long")
    assert problems["sections.1.description"].startswith("Texte trop long")
    assert problems["sections.1.fields"] == "Une liste de champs est attendue."
    assert problems["sections.2"] == "Section invalide."


def test_the_total_number_of_fields_is_bounded_across_sections():
    per_section = [[field(f"k{i}_{j}") for j in range(50)] for i in range(3)]
    problems = check_definition(sections_of(*per_section))
    assert problems["_"] == f"Au plus {MAX_FIELDS} champs."
    ok = check_definition(sections_of(*[[field(f"k{i}_{j}") for j in range(50)] for i in range(2)]))
    assert "_" not in ok


def test_a_duplicate_key_is_reported_on_the_second_field():
    problems = check_definition(definition(field("a"), field("a")))
    assert problems and all(path.startswith("sections.0.fields.1") for path in problems)


def test_rules_are_only_checked_once_the_structure_is_sound():
    broken = field("a", visible_if="values.inconnu == 1")
    bad_structure = {"sections": [{"id": "s", "title": "T", "fields": [broken, {"key": "?", "type": "inexistant"}]}]}
    assert all("visible_if" not in path for path in check_definition(bad_structure))
    problems = check_definition(definition(broken, field("b")))
    assert any(path.endswith("visible_if") for path in problems)


def test_a_field_cannot_read_itself_but_can_read_a_field_placed_in_another_section():
    ok = sections_of([field("a")], [field("b", visible_if="values.a == 'x'")])
    assert check_definition(ok) == {}
    self_read = definition(field("a", visible_if="values.a == 'x'"))
    assert any(path.endswith("visible_if") for path in check_definition(self_read))


def test_a_compute_expression_that_does_not_check_is_reported_on_the_field():
    problems = check_definition(definition(field("a", "number"), field("b", "number", compute="inconnue(a)")))
    assert any(path.endswith(".compute") for path in problems)


def test_a_loop_through_a_calculated_field_is_reported_last():
    d = definition(field("a", visible_if="values.b == 'x'"), field("b", compute="{a} & 'x'"))
    problems = check_definition(d)
    assert list(problems) == ["_"] and problems["_"].startswith("Ces champs se lisent en boucle")


def test_fields_that_read_each_other_for_their_conditions_are_accepted():
    d = definition(field("a", visible_if="values.b == 'x'"), field("b", visible_if="values.a == 'x'"))
    assert check_definition(d) == {}


def test_the_module_still_exposes_what_the_rest_of_the_application_imports():
    assert callable(formengine.check_definition) and formengine.BLOCK_TYPES
