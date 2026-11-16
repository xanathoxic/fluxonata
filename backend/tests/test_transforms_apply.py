"""Caractérisation de `workflows.transforms.apply` : chaque opération, ses refus, et l'ORDRE des vérifications
(la liste est évaluée avant les paramètres propres à l'opération ; `set` et `split` ne lisent pas de liste).

Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après."""

import pytest

from workflows.transforms import MAX_ITEMS, TransformError, apply

CTX = {
    "inputs": {
        "rows": [{"n": 3, "g": "a"}, {"n": 1, "g": "b"}, {"n": 2, "g": "a"}],
        "words": ["b", "a", "b"],
        "mixed": [1, "a"],
        "bools": [True, 2],
    },
}


def run(**node):
    return apply(node, CTX)


def test_an_unknown_operation_is_refused():
    with pytest.raises(TransformError, match="Opération inconnue : zip"):
        run(op="zip")
    with pytest.raises(TransformError, match="Opération inconnue : None"):
        apply({}, CTX)


def test_set_builds_its_own_fields_without_reading_a_list():
    assert run(op="set", fields={"a": "{{ inputs.words }}", "b": 1}) == {"a": ["b", "a", "b"], "b": 1}
    for bad in (None, {}, "x"):
        with pytest.raises(TransformError, match="au moins un champ"):
            run(op="set", fields=bad)


def test_set_reports_an_expression_error_as_a_transform_error():
    with pytest.raises(TransformError):
        run(op="set", fields={"a": "{{ inputs.absent.x }}"})


def test_chunk_cuts_into_batches_and_validates_its_size():
    assert run(op="chunk", items="inputs.words", size=2) == {"items": [["b", "a"], ["b"]], "count": 2}
    for bad in (0, -1, MAX_ITEMS + 1, "2", None, 1.5):
        with pytest.raises(TransformError, match="taille des lots"):
            run(op="chunk", items="inputs.words", size=bad)
    assert run(op="chunk", items="inputs.words", size=MAX_ITEMS)["count"] == 1


def test_the_list_is_checked_before_the_parameters_of_the_operation():
    # both the list and the size are wrong: the list is what is reported
    with pytest.raises(TransformError, match="« items » : indiquez la liste"):
        run(op="chunk", size=0)
    with pytest.raises(TransformError, match="doit être une liste"):
        run(op="limit", items="inputs.rows[0]", count=-5)


def test_map_makes_one_object_per_item():
    out = run(op="map", items="inputs.rows", fields={"double": "{{ item.n * 2 }}"})
    assert out == {"items": [{"double": 6}, {"double": 2}, {"double": 4}], "count": 3}
    with pytest.raises(TransformError, match="au moins un champ pour chaque élément"):
        run(op="map", items="inputs.rows", fields={})
    with pytest.raises(TransformError):
        run(op="map", items="inputs.rows", fields={"x": "{{ item.absent.y }}"})


def test_filter_keeps_the_items_where_the_condition_is_true():
    assert run(op="filter", items="inputs.rows", where="item.n > 1") == {
        "items": [{"n": 3, "g": "a"}, {"n": 2, "g": "a"}],
        "count": 2,
    }
    for bad in (None, "", "   "):
        with pytest.raises(TransformError, match="condition de filtre"):
            run(op="filter", items="inputs.rows", where=bad)


def test_sort_orders_ascending_by_default_and_descending_on_request():
    asc = run(op="sort", items="inputs.rows", by="item.n")["items"]
    assert [r["n"] for r in asc] == [1, 2, 3]
    desc = run(op="sort", items="inputs.rows", by="item.n", order="desc")["items"]
    assert [r["n"] for r in desc] == [3, 2, 1]
    assert run(op="sort", items="inputs.words")["items"] == ["a", "b", "b"]  # no key: the item itself


def test_sort_refuses_mixed_kinds_and_unsortable_values():
    with pytest.raises(TransformError, match="types différents"):
        run(op="sort", items="inputs.mixed")
    with pytest.raises(TransformError, match="types différents"):
        run(op="sort", items="inputs.bools")  # a boolean is not a number here
    with pytest.raises(TransformError, match="ne peuvent pas être triées"):
        run(op="sort", items="inputs.rows")  # objects of the same kind that cannot be compared


def test_unique_keeps_the_first_of_each_value_by_item_or_by_key():
    assert run(op="unique", items="inputs.words")["items"] == ["b", "a"]
    assert [r["n"] for r in run(op="unique", items="inputs.rows", by="item.g")["items"]] == [3, 1]


def test_limit_skips_then_takes_and_validates_both_numbers():
    assert run(op="limit", items="inputs.words", count=1, skip=1)["items"] == ["a"]
    assert run(op="limit", items="inputs.words", count=0)["items"] == []
    for count, skip in ((None, 0), (-1, 0), (1, -1), ("1", 0), (1, "0")):
        with pytest.raises(TransformError, match="entiers positifs"):
            run(op="limit", items="inputs.words", count=count, skip=skip)


def test_group_and_merge_are_delegated_with_the_list_already_read():
    grouped = run(
        op="group", items="inputs.rows", by="item.g", aggregates=[{"name": "total", "fn": "sum", "expr": "item.n"}]
    )
    assert grouped["count"] == 2
    assert {g["key"]: g["total"] for g in grouped["items"]} == {"a": 5, "b": 1}
    merged = run(op="merge", items="inputs.rows", right="inputs.rows", merge_mode="append")
    assert merged["count"] == 6
    with pytest.raises(TransformError):
        run(op="group", items="inputs.rows")  # a grouping needs its key
