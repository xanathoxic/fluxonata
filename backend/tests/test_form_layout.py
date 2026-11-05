from tests.form_helpers import definition, field
from tests.wf_helpers import schema
from triggers import formengine


def at(row, col, span, **f):
    return {**field(f.pop("key"), **f), "layout": {"row": row, "col": col, "span": span}}


def test_layout_shape_and_ranges():
    ok = definition(at(1, 1, 6, key="a"), at(1, 7, 6, key="b"), at(2, 1, 12, key="c"))
    assert formengine.check_definition(ok) == {}
    for name, lay in {
        "colonne hors grille": (1, 13, 1),
        "largeur qui déborde": (1, 7, 7),
        "ligne nulle": (0, 1, 1),
        "ligne trop grande": (201, 1, 1),
        "largeur nulle": (1, 1, 0),
    }.items():
        assert formengine.check_definition(definition(at(*lay, key="a"))), name
    bad_shape = definition({**field("a"), "layout": {"row": 1, "col": 1}})
    assert formengine.check_definition(bad_shape)
    assert formengine.check_definition(definition({**field("a"), "layout": {"row": 1.5, "col": 1, "span": 1}}))
    assert formengine.check_definition(definition({**field("a"), "layout": {"row": True, "col": 1, "span": 1}}))


def test_two_fields_of_a_row_never_share_a_column():
    assert formengine.check_definition(definition(at(1, 1, 6, key="a"), at(1, 6, 3, key="b")))
    assert formengine.check_definition(definition(at(1, 1, 6, key="a"), at(2, 6, 3, key="b"))) == {}


def test_reading_order_is_row_then_column_and_unplaced_fields_come_after():
    d = definition(at(2, 1, 12, key="c"), field("loose"), at(1, 7, 6, key="b"), at(1, 1, 6, key="a"))
    assert [f["key"] for f in formengine.iter_fields(d)] == ["a", "b", "c", "loose"]


def test_a_rule_reads_any_other_field_wherever_it_is_laid_out():
    # `a` on the second row reads `b` on the first row; `b` side by side or below may read `a` too, but not both ways
    d = definition(
        {**at(2, 1, 12, key="a"), "visible_if": "values.b == 'x'"},
        at(1, 1, 12, key="b"),
    )
    assert formengine.check_definition(d) == {}
    below = definition(
        {**at(1, 1, 12, key="a"), "visible_if": "values.b == 'x'"},
        at(2, 1, 12, key="b"),
    )
    assert formengine.check_definition(below) == {}
    loop = definition(
        {**at(1, 1, 6, key="a"), "visible_if": "values.b == 'x'"},
        {**at(1, 7, 6, key="b"), "visible_if": "values.a == 'x'"},
    )
    assert "boucle" in formengine.check_definition(loop)["_"]
    kept, errors = formengine.validate_submission(d, {"a": "v", "b": "x"})
    assert kept == {"a": "v", "b": "x"} and errors == {}
    assert formengine.validate_submission(d, {"a": "v", "b": "y"})[0] == {"b": "y"}
    assert formengine.validate_submission(below, {"a": "v", "b": "x"})[0] == {"a": "v", "b": "x"}


def test_content_blocks_carry_no_value_and_no_binding():
    d = definition(
        {"key": "t", "type": "heading", "label": "Votre demande", "level": 2},
        {"key": "p", "type": "paragraph", "label": "Merci de **remplir** ce qui suit."},
        {"key": "sep", "type": "divider"},
        field("sujet", required=True),
    )
    assert formengine.check_definition(d) == {}
    s = schema(["sujet"], sujet={"type": "string"})
    assert formengine.check_against_inputs(d, s) == {}  # the blocks are not inputs of the workflow
    kept, errors = formengine.validate_submission(d, {"sujet": "x", "t": "injecté", "p": "injecté"})
    assert kept == {"sujet": "x"} and errors == {}
    assert [f["key"] for f in formengine.data_fields(d)] == ["sujet"]


def test_blocks_refuse_what_only_a_field_can_have():
    for extra in (
        {"required": True},
        {"options": [{"value": "a"}]},
        {"source": {"connection": "c", "path": "/"}},
        {"placeholder": "x"},
    ):
        assert formengine.check_definition(definition({"key": "t", "type": "heading", "label": "T", **extra})), extra
    assert formengine.check_definition(definition({"key": "t", "type": "heading", "label": "T", "level": 1}))
    assert formengine.check_definition(definition({"key": "t", "type": "heading"}))  # a heading needs a text
    assert formengine.check_definition(definition({**field("a"), "level": 2}))  # a level is for headings
    assert formengine.check_definition(definition({"key": "t", "type": "paragraph", "label": "x" * 2001}))
    assert formengine.check_definition(definition({"key": "t", "type": "paragraph", "label": "x" * 2000})) == {}


def with_style(style):
    return {**definition(field("a")), "style": style}


def test_style_is_a_closed_set_of_choices_and_one_readable_colour():
    good = {
        "accent": "#0e7490",
        "density": "compact",
        "labels": "left",
        "radius": "large",
        "width": "wide",
        "sections": "line",
        "button": "full",
    }
    assert formengine.check_definition(with_style(good)) == {}
    assert formengine.check_definition(with_style({})) == {}
    for name, style in {
        "clé inconnue": {"css": "x"},
        "couleur non hexadécimale": {"accent": "red"},
        "couleur avec injection": {"accent": "#000000; background:url(x)"},
        "couleur courte": {"accent": "#fff"},
        "couleur trop claire": {"accent": "#ffff00"},
        "densité inconnue": {"density": "huge"},
        "libellés inconnus": {"labels": "bottom"},
        "largeur inconnue": {"width": "100%"},
        "style non objet": "dark",
    }.items():
        assert formengine.check_definition(with_style(style)), name


def test_contrast_helper():
    assert round(formengine.contrast_with_white("#000000"), 1) == 21.0
    assert round(formengine.contrast_with_white("#ffffff"), 1) == 1.0
    assert formengine.contrast_with_white("#0e7490") >= 4.5


def tall(row, col, span, height, **f):
    return {**field(f.pop("key"), **f), "layout": {"row": row, "col": col, "span": span, "height": height}}


def test_a_field_can_cover_several_rows_and_nothing_overlaps_it():
    # a long text on the right covers two rows; two short fields stack on its left: no wasted space
    ok = definition(at(1, 1, 6, key="a"), at(2, 1, 6, key="b"), tall(1, 7, 6, 2, key="c", type="textarea"))
    assert formengine.check_definition(ok) == {}
    # a field under it, in its columns, overlaps it
    assert formengine.check_definition(definition(tall(1, 7, 6, 2, key="c", type="textarea"), at(2, 9, 2, key="d")))
    # ... and beside it, or below its last row, is fine
    assert (
        formengine.check_definition(definition(tall(1, 7, 6, 2, key="c", type="textarea"), at(3, 7, 6, key="d"))) == {}
    )
    # an older form (no height) keeps its meaning: one row
    assert formengine.check_definition(definition(at(1, 1, 12, key="a", type="textarea"), at(2, 1, 12, key="b"))) == {}


def test_height_is_checked():
    for h in (0, 9, -1, 1.5, True, "2"):
        assert formengine.check_definition(
            definition({**field("a"), "layout": {"row": 1, "col": 1, "span": 12, "height": h}})
        ), h
    assert (
        formengine.check_definition(definition({**field("a"), "layout": {"row": 1, "col": 1, "span": 12, "height": 8}}))
        == {}
    )
    assert formengine.check_definition(
        definition({**field("a"), "layout": {"row": 1, "col": 1, "span": 12, "high": 2}})
    )


def test_unplaced_fields_start_below_the_bottom_of_tall_ones():
    d = definition(tall(1, 1, 12, 3, key="c", type="textarea"), field("loose"))
    keys = [f["key"] for f in formengine.iter_fields(d)]
    assert keys == ["c", "loose"]
    placed = {f["key"] for f in formengine.iter_fields(d)}
    assert placed == {"c", "loose"}
