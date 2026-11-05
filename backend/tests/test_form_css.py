import pytest

from tests.conftest import client_for
from tests.form_helpers import BASE, definition, field
from triggers import formcss, formengine

GOOD = """
/* titres */
.fb-heading { color: #1d4ed8; font-size: 1.4rem; letter-spacing: .02em }
.fb-field label, label { font-weight: 700; }
input:focus-visible { outline: 2px solid #1d4ed8; box-shadow: 0 0 0 3px rgba(29,78,216,.25) }
[data-key="nom"] input { background: linear-gradient(90deg, #fff, #eef); border-radius: calc(var(--fb-radius) + 2px) }
&:hover { opacity: .98 }
.fb-actions > button + button { margin-left: 8px }
@media (max-width: 600px) { .fb-section { padding: 8px } }
@media (prefers-color-scheme: dark) { .fb-paragraph { color: #cbd5e1 } }
input::placeholder { color: #64748b; font-style: italic }
.fb-cell:nth-child(2n+1) .fb-field { margin: 0 }
h3::before { content: "→ "; }
"""


def test_a_reasonable_stylesheet_is_accepted_and_rewritten_canonically():
    css, problems = formcss.normalize(GOOD)
    assert problems == [] and css
    assert css.count("{") == css.count("}")
    assert all(line.startswith(("&", "@media", "  ", "}", "")) for line in css.split("\n"))
    assert "/*" not in css and "titres" not in css  # comments are dropped
    assert "& .fb-field label,\n& label {" in css  # every selector starts with the form's root
    assert "& input:focus-visible {" in css and "&:hover {" in css and "& .fb-actions > button + button {" in css
    assert "@media (max-width: 600px) {\n  & .fb-section {" in css
    assert formcss.normalize(css) == (css, [])  # normalising is idempotent


@pytest.mark.parametrize(
    "src",
    [
        ".a { background: url(https://evil.example/x.png) }",
        ".a { background-image: url('x') }",
        ".a { background: URL(x) }",
        ".a { background: image-set(x 1x) }",
        ".a { content: attr(data-key) }",
        ".a { width: env(safe-area-inset-left) }",
        ".a { width: expression(alert(1)) }",
        ".a { behavior: url(x.htc) }",
        ".a { -moz-binding: url(x) }",
        ".a { color: red !important }",
        ".a { position: fixed; top: 0; left: 0 }",
        ".a { position: absolute }",
        ".a { z-index: 99999 }",
        ".a { pointer-events: none }",
        ".a { visibility: hidden }",
        ".a { display: contents }",
        ".a { cursor: url(x), auto }",
        '.a { font-family: "x"; src: local(x) }',
        ".a { content: counter(x) }",
        ".a { width: calc(url(x)) }",
        ".a { width: 10foo }",
        ".a { --accent: yellow }",
        ".a { --x: 1 }",
        ".a { color: red; }}",
        ".a { color: red",
        "color: red;",
        ".a { }",
        ".a { color red }",
        ".a { color: ; }",
        "@import url(x.css);",
        "@import 'x.css';",
        "@font-face { font-family: x; src: url(x.woff) }",
        "@keyframes x { from { color: red } to { color: blue } }",
        "@supports (display: grid) { .a { color: red } }",
        "@media (max-width: 600px) { @media (min-width: 100px) { .a { color: red } } }",
        "@media screen { .a { color: red } }",
        "@media (max-width: 600px) and (orientation: landscape) { .a { color: red } }",
        ".a { .b { color: red } }",
        ".a { color: red; .b { color: blue } }",
        "body { color: red }",
        "html { color: red }",
        ":root { color: red }",
        "script { color: red }",
        "iframe { color: red }",
        "a { color: red }",
        "input[value^='a'] { color: red }",
        "input[type=password][value*=x] { color: red }",
        ".a :has(input) { color: red }",
        ".a :is(input) { color: red }",
        ".a:not(.b) { color: red }",
        ".x &, & { color: red }",
        ".x & .y { color: red }",
        ".a; { color: red }",
        ".a { color: red } .b ;",
        ".a\\62 { color: red }",
        ".a { color: \\72ed }",
        ".a { color: red } </style><script>alert(1)</script>",
        "<style>.a{color:red}</style>",
        ".a { color: red } /* unclosed",
        ".a { background: red; } /**/ } .b",
        ".a { color: red\u0000 }",
        '.a { content: "x\ny" }',
    ],
)
def test_hostile_or_unsupported_css_is_refused(src):
    css, problems = formcss.normalize(src)
    assert problems and css == "", src
    assert problems[0]["line"] >= 1 and problems[0]["message"]


def test_nothing_of_the_original_text_survives_but_validated_tokens():
    # whatever the author writes between the lines, the output holds only what the grammar produced
    css, problems = formcss.normalize(".a{color:red;/*}*/background:#fff}")
    assert problems == [] and css == "& .a {\n  color: red;\n  background: #fff;\n}"
    css, problems = formcss.normalize(".a { color : RED ;  FONT-WEIGHT:700 }")
    assert problems == [] and css == "& .a {\n  color: RED;\n  font-weight: 700;\n}"


def test_limits():
    assert formcss.normalize("x" * 20_001)[1]
    many = "\n".join(f".r{i} {{ color: red }}" for i in range(201))
    assert formcss.normalize(many)[1]
    assert formcss.normalize("\n".join(f".r{i} {{ color: red }}" for i in range(200)))[1] == []
    wide = ".a { " + " ".join(f"margin: {i}px;" for i in range(41)) + " }"
    assert formcss.normalize(wide)[1]
    assert formcss.normalize(", ".join(f".c{i}" for i in range(11)) + " { color: red }")[1]


def test_errors_say_which_line():
    src = ".ok { color: red }\n\n.bad { background: url(x) }\n.ok2 { color: blue }"
    _, problems = formcss.normalize(src)
    assert problems[0]["line"] == 3 and "url" in problems[0]["message"]


def test_empty_and_missing_css_are_valid():
    assert (
        formcss.normalize("") == ("", [])
        and formcss.normalize(None) == ("", [])
        and formcss.normalize("  \n ") == ("", [])
    )
    assert formcss.normalize(12)[1]


def test_the_definition_only_accepts_the_canonical_text():
    canonical, _ = formcss.normalize(".a { color: red }")
    base = definition(field("a"))
    assert formengine.check_definition({**base, "css": canonical}) == {}
    assert "css" in formengine.check_definition(
        {**base, "css": ".a{color:red}"}
    )  # valid CSS, but not the canonical text
    assert "css" in formengine.check_definition({**base, "css": "& .a {\n  background: url(x);\n}"})
    assert "css" in formengine.check_definition({**base, "css": 12})


def test_saving_normalizes_and_refuses_hostile_css(world):
    c = client_for(world["designer"])
    L = f"{BASE}/form-library"
    d = {**definition(field("sujet")), "css": ".fb-heading{color:#1d4ed8}/*note*/"}
    r = c.post(f"{L}/", {"slug": "f", "name": "F", "definition": d}, format="json")
    assert r.status_code == 201
    saved = c.get(f"{L}/f/").data["definition"]["css"]
    assert saved == "& .fb-heading {\n  color: #1d4ed8;\n}"
    bad = c.patch(
        f"{L}/f/", {"definition": {**d, "css": ".a{background:url(//evil.example)}"}, "revision": 1}, format="json"
    )
    assert bad.status_code == 400 and "css" in str(bad.json())
    assert c.get(f"{L}/f/").data["draft_revision"] == 1  # nothing was stored
    # an empty CSS removes the key
    ok = c.patch(f"{L}/f/", {"definition": {**d, "css": ""}, "revision": 1}, format="json")
    assert ok.status_code == 200 and "css" not in c.get(f"{L}/f/").data["definition"]


def test_check_endpoint_gives_feedback_without_storing(world):
    c = client_for(world["designer"])
    r = c.post(f"{BASE}/form-library/check/", {"css": ".a{color:red}"}, format="json").json()
    assert r["css"] == {"css": "& .a {\n  color: red;\n}", "problems": []}
    bad = c.post(f"{BASE}/form-library/check/", {"css": ".a{background:url(x)}"}, format="json").json()
    assert bad["css"]["css"] == "" and bad["css"]["problems"][0]["line"] == 1
    d = c.post(f"{BASE}/form-library/check/", {"definition": {"sections": []}}, format="json").json()
    assert d["definition"]
    assert (
        client_for(world["viewer"]).post(f"{BASE}/form-library/check/", {"css": "x"}, format="json").status_code == 403
    )
    assert (
        client_for(world["outsider"]).post(f"{BASE}/form-library/check/", {"css": "x"}, format="json").status_code
        == 404
    )
