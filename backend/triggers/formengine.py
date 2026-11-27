"""Form definitions: structure checks, link with the workflow's inputs, visibility and server-side validation.

A definition is plain JSON (sections of fields). Every field is bound to one input of the workflow (`key` = input name),
so a submission is always checked twice: by the form (what the page asks) and by the workflow's input schema.
Visibility rules use the workflow expression language, over `values` only, and may look at any other field, wherever it is
placed. Fields are evaluated after the ones they read; fields that read each other in a loop (A reads B which reads A) are evaluated
again, up to MAX_PASSES times, until nothing changes. A loop through a calculated value is refused.
"""

import re
from datetime import date
from typing import cast

from workflows.expressions import ExpressionError, evaluate_ast, parse, paths_in

from . import formcalc, formcontrols, formcss, formrules, shaping

FIELD_TYPES = {
    "text": "string",
    "textarea": "string",
    "email": "string",
    "date": "string",
    "number": "number",
    "select": "string",
    "radio": "string",
    "combobox": "string",
    "checkbox": "boolean",
    "checkbox_list": "array",
    "datagrid": "array",
    "custom": "object",  # the real type depends on the control: see produces()
}
CHOICE_TYPES = {"select", "radio", "combobox", "checkbox_list"}
# content blocks: text and separators of the page. They carry no value, are not bound to an input and cannot be required.
BLOCK_TYPES = {"heading", "paragraph", "divider"}
GRID_COLUMNS, MAX_ROWS_GRID, MAX_HEIGHT = 12, 200, 8
MAX_SECTIONS, MAX_FIELDS, MAX_OPTIONS, MAX_COLUMNS, MAX_ROWS = 20, 100, 200, 12, 100
MAX_TEXT, MAX_LONG_TEXT = 1000, 10_000
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SOURCE_KEYS = (
    {"connection", "path", "items_path", "label_path", "value_path", "params"}
    | shaping.SHAPING_KEYS
    | shaping.CONNECTION_KEYS
)
FIELD_KEYS = {
    "key",
    "label",
    "type",
    "required",
    "help",
    "placeholder",
    "default",
    "options",
    "min",
    "max",
    "visible_if",
    "source",
    "columns",
    "layout",
    "level",
    "rules",
    "compute",
    "control",
    "prefill",
    "display",
    "available_label",
    "chosen_label",
}
BLOCK_KEYS = {"key", "label", "type", "visible_if", "layout", "level", "rules"}
SECTION_KEYS = {"id", "title", "description", "fields"}
CHECKBOX_DISPLAYS = (
    "list",
    "dual",
)  # « dual » : two lists (available / chosen) where the chosen ones can be put in order


def _text(value, limit) -> bool:
    return isinstance(value, str) and len(value) <= limit


def placed(section: dict) -> list[dict]:
    """The fields of a section in reading order: by row, then column, as laid out. A field without a `layout` takes a row
    of its own after the laid-out ones, in the order it is listed (the browser applies the very same rule)."""
    fields = [f for f in section.get("fields", []) if isinstance(f, dict)]
    # a field without a layout starts below the bottom of the laid-out ones (a tall field counts for its whole height)
    last = max((_lay(f)[0] + _height(f) - 1 for f in fields if isinstance(f.get("layout"), dict)), default=0)
    keyed = []
    for i, f in enumerate(fields):
        if isinstance(f.get("layout"), dict):
            row, col, _ = _lay(f)
        else:
            last += 1
            row, col = last, 1
        keyed.append((row, col, i, f))
    return [t[3] for t in sorted(keyed, key=lambda t: t[:3])]


def _height(field: dict) -> int:
    """Rows a field covers: the `height` of its layout, 1 when it has none (older forms)."""
    h = (field.get("layout") or {}).get("height")
    return h if isinstance(h, int) and not isinstance(h, bool) and 1 <= h <= MAX_HEIGHT else 1


def _lay(field: dict) -> tuple[int, int, int]:
    lay = field.get("layout") or {}
    row, col, span = lay.get("row"), lay.get("col"), lay.get("span")
    ok = all(isinstance(v, int) and not isinstance(v, bool) for v in (row, col, span))
    return (
        cast("tuple[int, int, int]", (row, col, span)) if ok else (0, 1, GRID_COLUMNS)
    )  # `ok` : trois entiers vérifiés


def iter_fields(definition: dict):
    """Every field (and block) in reading order: sections as listed, each laid out row by row."""
    for section in definition.get("sections", []):
        yield from placed(section)


def iter_sources(definition: dict):
    """(field key, reading of a service) of every dynamic list and of every field that reads a service (a control's options, its
    pre-fill): the reading belongs to the form field, never to the control."""
    for f in iter_fields(definition):
        for name in ("source", "prefill"):
            if isinstance(f.get(name), dict):
                yield f["key"], f[name]


def data_fields(definition: dict):
    """The fields that carry a value (not the content blocks)."""
    return (f for f in iter_fields(definition) if f.get("type") not in BLOCK_TYPES)


def _check_envelope(definition) -> dict[str, str]:
    """The shape of the whole definition (known keys, reference, input map, number of sections): one problem stops the check."""
    if not isinstance(definition, dict) or set(definition) - {
        "version",
        "sections",
        "ref",
        "map",
        "style",
        "css",
        "submit",
    }:
        return {"_": "Définition invalide : un objet {version, sections} est attendu."}
    ref = definition.get("ref")
    if ref is not None and not (
        isinstance(ref, dict)
        and set(ref) <= {"form", "version"}
        and _text(ref.get("form"), 60)
        and (ref.get("version") is None or _text(ref.get("version"), 20))
    ):
        return {"ref": "Référence de formulaire invalide."}
    mapping = definition.get("map")
    if mapping is not None and not _valid_map(mapping):
        return {"map": "Correspondance des entrées invalide : {entrée du workflow: clé d'un champ}."}
    sections = definition.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        return {"sections": f"Entre 1 et {MAX_SECTIONS} sections."}
    return {}


def _check_section(section, where, seen, problems) -> bool:
    """One section: its own keys and texts, then its fields and layout. False once the total number of fields is exceeded."""
    if not isinstance(section, dict) or set(section) - SECTION_KEYS:
        problems[where] = "Section invalide."
        return True
    if not _text(section.get("title", ""), 160):
        problems[f"{where}.title"] = "Titre trop long (160 caractères)."
    if not _text(section.get("description", ""), 2000):
        problems[f"{where}.description"] = "Texte trop long (2000 caractères)."
    fields = section.get("fields")
    if not isinstance(fields, list):
        problems[f"{where}.fields"] = "Une liste de champs est attendue."
        return True
    for fi, field in enumerate(fields):
        if len(seen) >= MAX_FIELDS:
            problems["_"] = f"Au plus {MAX_FIELDS} champs."
            return False
        path = f"{where}.fields.{fi}"
        problems.update(_check_field(field, path, seen))
        if isinstance(field, dict) and isinstance(field.get("key"), str):
            seen.append(field["key"])
    problems.update(_check_layout(fields, where))
    return True


def _check_cross_references(sections) -> dict[str, str]:
    """Rules, visibility and computed values may read any OTHER data field, wherever it is placed (side by side, below…)."""
    problems: dict[str, str] = {}
    every = {f["key"]: f for s_ in sections for f in s_["fields"] if f.get("type") not in BLOCK_TYPES}
    for si, section in enumerate(sections):
        for fi, field in enumerate(section["fields"]):
            where = f"sections.{si}.fields.{fi}"
            rule = field.get("visible_if")
            if rule not in (None, ""):
                problems.update(_check_rule(rule, f"{where}.visible_if", [k for k in every if k != field["key"]]))
            others = {k: f for k, f in every.items() if k != field["key"]}
            # the rules may also read the field itself (its own value); a formula and a legacy expression may not
            problems.update(formrules.check_rules(field.get("rules"), f"{where}.rules", field, every))
            if "compute" in field and (message := formcalc.check(field["compute"], others)):
                problems[f"{where}.compute"] = message
    return problems


SUBMIT_KEY = "_submit"
SUBMIT_FIELD = {
    "key": SUBMIT_KEY,
    "type": "submit",
    "label": "Bouton d'envoi",
}  # le bouton se règle comme un champ sans valeur


def _check_submit(submit, every: dict[str, dict]) -> dict[str, str]:
    """Conditions du bouton d'envoi : `{rules: [...]}`, les mêmes règles que celles d'un champ (elles lisent les champs de données)."""
    if submit is None:
        return {}
    if not isinstance(submit, dict) or set(submit) - {"rules"}:
        return {"submit": "Bouton d'envoi invalide : {rules} attendu."}
    return formrules.check_rules(submit.get("rules"), "submit.rules", SUBMIT_FIELD, every)


def submit_state(definition: dict, values: dict) -> dict:
    """État du bouton d'envoi (visible, bloqué = `readonly`, libellé `label`, message `help`) pour ces valeurs (celles que le formulaire garde)."""
    rules = (definition.get("submit") or {}).get("rules")
    field = {**SUBMIT_FIELD, "rules": rules}
    return formrules.apply_rules(field, values, formrules.initial_state(field))


def check_definition(definition) -> dict[str, str]:
    """Structure only (no workflow needed). Returns {path: message}, empty when the definition is well formed.

    The checks run in stages, each only once the previous one found nothing to report: the envelope, the sections and their fields,
    then what the fields say about each other (rules, visibility, computed values), then loops."""
    if problems := _check_envelope(definition):
        return problems
    if definition.get("style") is not None:
        problems.update(_check_style(definition["style"]))
    if definition.get("css") not in (None, ""):
        problems.update(_check_css(definition["css"]))
    seen: list[str] = []
    for si, section in enumerate(definition["sections"]):
        if not _check_section(section, f"sections.{si}", seen, problems):
            return problems
    if problems:
        return problems
    if problems := _check_cross_references(definition["sections"]):
        return problems
    every = {f["key"]: f for s_ in definition["sections"] for f in s_["fields"] if f.get("type") not in BLOCK_TYPES}
    if problems := _check_submit(definition.get("submit"), every):
        return problems
    if loop := _find_loop(every, list(_all_fields(definition))):
        problems["_"] = "Ces champs se lisent en boucle : " + " → ".join(loop) + "."
    return problems


def _all_fields(definition: dict):
    for section in definition["sections"]:
        yield from section["fields"]


def dependencies(field: dict) -> set[str]:
    """Keys of the fields this one reads: its rules and its (legacy) visibility expression."""
    out = (formrules.reads(field) - {field.get("key")}) | formcalc.refs(
        field.get("compute")
    )  # reading itself is no dependency
    rule = field.get("visible_if")
    if isinstance(rule, str) and rule:
        try:
            out.update(p[1] for p in paths_in(parse(rule)) if len(p) > 1)
        except ExpressionError:
            pass
    return out


def _path_back(deps: dict[str, list[str]], start: str) -> list[str]:
    """A chain of readings that leads from `start` back to itself, or []."""
    seen: set[str] = set()
    stack = [(start, [start])]
    while stack:
        key, path = stack.pop()
        for d in deps.get(key, []):
            if d == start:
                return [*path, d]
            if d not in seen:
                seen.add(d)
                stack.append((d, [*path, d]))
    return []


def _find_loop(every: dict[str, dict], fields: list[dict]) -> list[str]:
    """A loop through a calculated field (its value would depend on itself). Fields that only read each other for their conditions may loop:
    their evaluation is repeated until it is stable (`field_states`)."""
    deps = {f["key"]: sorted(dependencies(f) & set(every)) for f in fields}
    for field in fields:
        if "compute" in field and (found := _path_back(deps, field["key"])):
            return found
    return []


def evaluation_order(fields: list[dict]) -> list[dict]:
    """Fields so that each comes after the ones it reads (ties: reading order). Loops are refused when the definition
    is checked; should one remain, its fields simply follow in reading order."""
    keys = {f["key"] for f in fields}
    deps = {f["key"]: dependencies(f) & keys for f in fields}
    done: set[str] = set()
    out: list[dict] = []
    left = list(fields)
    while left:
        ready = next((f for f in left if deps[f["key"]] <= done), left[0])
        left.remove(ready)
        done.add(ready["key"])
        out.append(ready)
    return out


def _check_key(field: dict, path: str, earlier: list[str]) -> dict[str, str]:
    """The key: well formed and not used by an earlier field."""
    out: dict[str, str] = {}
    key = field.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        out[f"{path}.key"] = "Clé invalide (lettres, chiffres et _ ; 64 caractères au plus)."
    elif key in earlier:
        out[f"{path}.key"] = f"La clé « {key} » est déjà utilisée."
    return out


def _check_block(field: dict, path: str, kind: str) -> dict[str, str]:
    """A content block (title, paragraph, divider): its text, level and visibility."""
    out: dict[str, str] = {}
    limit = 2000 if kind == "paragraph" else 160
    if not _text(field.get("label", ""), limit) or (kind != "divider" and not field.get("label")):
        out[f"{path}.label"] = f"Un texte de {limit} caractères au plus est obligatoire."
    if "level" in field and field["level"] not in (2, 3, 4):
        out[f"{path}.level"] = "Niveau de titre : 2, 3 ou 4."
    rule = field.get("visible_if")
    if rule not in (None, "") and not isinstance(rule, str):
        out[f"{path}.visible_if"] = "Une expression est attendue."
    return out


def _check_common(field: dict, path: str) -> dict[str, str]:
    """What every input field has: level (title only), label, help, required, numeric bounds."""
    out: dict[str, str] = {}
    if "level" in field:
        out[f"{path}.level"] = "Seul un titre a un niveau."
    if not _text(field.get("label"), 160) or not field.get("label"):
        out[f"{path}.label"] = "Un libellé de 160 caractères au plus est obligatoire."
    for name in ("help", "placeholder"):
        if not _text(field.get(name, ""), 500):
            out[f"{path}.{name}"] = "Texte trop long (500 caractères)."
    if not isinstance(field.get("required", False), bool):
        out[f"{path}.required"] = "Vrai ou faux attendu."
    for bound in ("min", "max"):
        v = field.get(bound)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            out[f"{path}.{bound}"] = "Un nombre est attendu."
    return out


def _check_dual_list(field: dict, path: str, kind: str) -> dict[str, str]:
    """Column titles and display of a list of boxes (the double list is for multiple boxes only)."""
    out: dict[str, str] = {}
    for name in ("available_label", "chosen_label"):
        if name in field and (
            kind != "checkbox_list"
            or field.get("display") != "dual"
            or not _text(field[name], formcontrols.MAX_TITLE)
            or not field[name].strip()
        ):
            out[f"{path}.{name}"] = (
                f"Titre de colonne de {formcontrols.MAX_TITLE} caractères au plus, pour la liste double seulement."
            )
    if "display" in field and (kind != "checkbox_list" or field["display"] not in CHECKBOX_DISPLAYS):
        out[f"{path}.display"] = "Affichage : « list » ou « dual » (liste double), pour les cases multiples seulement."
    return out


def _check_kind_features(field: dict, path: str, kind: str, found: dict[str, str]) -> dict[str, str]:
    """What depends on the kind of field: custom control, column titles, display, options, columns, visibility, formula."""
    out: dict[str, str] = {}
    if kind == "custom":
        out.update(formcontrols.check_field(field, path))
        if "options" in field and not (found or out):  # options are read only when nothing else is wrong yet
            out.update(_check_choices(field, path))
    elif "control" in field or "prefill" in field:
        out[f"{path}.control"] = "Seul un contrôle personnalisé porte un contrôle et un remplissage par un service."
    out.update(_check_dual_list(field, path, kind))
    if kind in CHOICE_TYPES:
        out.update(_check_choices(field, path))
    elif kind != "custom" and ("options" in field or "source" in field):
        out[f"{path}.options"] = "Seuls les champs à choix ont des options."
    if kind == "datagrid":
        out.update(_check_columns(field, path))
    elif "columns" in field:
        out[f"{path}.columns"] = "Seule une grille a des colonnes."
    rule = field.get("visible_if")
    if rule not in (None, "") and not isinstance(rule, str):
        out[f"{path}.visible_if"] = "Une expression est attendue."
    if "compute" in field and kind not in formcalc.COMPUTABLE:
        out[f"{path}.compute"] = "Ce type de champ ne peut pas être calculé (texte, nombre, date ou e-mail)."
    return out


def _check_field(field, path: str, earlier: list[str]) -> dict[str, str]:
    if not isinstance(field, dict) or set(field) - FIELD_KEYS:
        return {path: "Champ invalide."}
    if field.get("type") in BLOCK_TYPES and set(field) - BLOCK_KEYS:
        return {path: "Un bloc de contenu n'a ni option, ni source, ni contrainte."}
    out = _check_key(field, path, earlier)
    kind = field.get("type")
    if kind not in FIELD_TYPES and kind not in BLOCK_TYPES:
        out[f"{path}.type"] = "Type de champ inconnu."
        return out
    if "layout" in field:
        out.update(_check_one_layout(field["layout"], f"{path}.layout"))
    if kind in BLOCK_TYPES:
        return {**out, **_check_block(field, path, kind)}
    out.update(_check_common(field, path))
    out.update(_check_kind_features(field, path, kind, out))
    return out


def _check_one_layout(lay, path: str) -> dict[str, str]:
    if not isinstance(lay, dict) or not {"row", "col", "span"} <= set(lay) <= {"row", "col", "span", "height"}:
        return {path: "Placement invalide : {row, col, span} et, facultativement, height attendus."}
    row, col, span = (lay[k] for k in ("row", "col", "span"))
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (row, col, span, lay.get("height", 1))):
        return {path: "Le placement se compose de nombres entiers."}
    if not 1 <= lay.get("height", 1) <= MAX_HEIGHT:
        return {path: f"Hauteur de 1 à {MAX_HEIGHT} lignes."}
    if not 1 <= row <= MAX_ROWS_GRID or not 1 <= col <= GRID_COLUMNS or not 1 <= span <= GRID_COLUMNS - col + 1:
        return {
            path: f"Ligne de 1 à {MAX_ROWS_GRID}, colonne de 1 à {GRID_COLUMNS}, largeur sans dépasser la colonne {GRID_COLUMNS}."
        }
    return {}


def _check_layout(fields: list, where: str) -> dict[str, str]:
    """Two fields never share a cell of the grid: rectangles of columns × rows (a field covers `height` rows)."""
    placed_rects: list[tuple[int, int, int, int]] = []
    for i, f in enumerate(fields):
        if not isinstance(f, dict) or not isinstance(f.get("layout"), dict) or _check_one_layout(f["layout"], ""):
            continue
        row, col, span = _lay(f)
        r1, c1 = row + _height(f) - 1, col + span - 1
        for pr0, pr1, pc0, pc1 in placed_rects:
            if row <= pr1 and pr0 <= r1 and col <= pc1 and pc0 <= c1:
                return {
                    f"{where}.fields.{i}.layout": f"Ce champ chevauche un autre champ (ligne {row}, colonne {col})."
                }
        placed_rects.append((row, r1, col, c1))
    return {}


STYLE_ENUMS = {
    "display": {"standard", "page", "modern"},
    "density": {"compact", "normal", "comfortable"},
    "labels": {"top", "left"},
    "radius": {"none", "small", "medium", "large"},
    "width": {"narrow", "medium", "wide", "full"},
    "sections": {"card", "plain", "line"},
    "button": {"left", "center", "right", "full"},
}
MAX_SIDE = 40  # marge de chaque côté, en % de la largeur disponible (mode moderne)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_CONTRAST = 4.5


def _luminance(hexcolor: str) -> float:
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hexcolor[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_with_white(hexcolor: str) -> float:
    return 1.05 / (_luminance(hexcolor) + 0.05)


def _check_css(css) -> dict[str, str]:
    """The stored CSS must already be the canonical text the server wrote (see `formcss.normalize`): anything else, edited by
    hand in an import for instance, is refused rather than interpreted."""
    canonical, problems = formcss.normalize(css)
    if problems:
        return {"css": f"Ligne {problems[0]['line']} : {problems[0]['message']}"}
    if canonical != css:
        return {"css": "CSS non normalisé : enregistrez-le depuis l'éditeur."}
    return {}


def _check_style(style) -> dict[str, str]:
    """How the form looks. Closed set of choices and one validated colour: nothing here can inject CSS."""
    if not isinstance(style, dict) or set(style) - (set(STYLE_ENUMS) | {"accent", "side"}):
        return {"style": "Style invalide."}
    out: dict[str, str] = {}
    for key, allowed in STYLE_ENUMS.items():
        if key in style and not (isinstance(style[key], str) and style[key] in allowed):
            out[f"style.{key}"] = f"Valeur inconnue (parmi : {', '.join(sorted(allowed))})."
    side = style.get("side")
    if "side" in style and not (isinstance(side, int) and not isinstance(side, bool) and 0 <= side <= MAX_SIDE):
        out["style.side"] = f"Marge latérale : un entier de 0 à {MAX_SIDE} (en % de la largeur)."
    accent = style.get("accent")
    if accent is not None:
        if not isinstance(accent, str) or not HEX_RE.match(accent):
            out["style.accent"] = "Couleur invalide (format #rrggbb)."
        elif contrast_with_white(accent) < MIN_CONTRAST:
            out["style.accent"] = (
                "Couleur trop claire : le texte blanc des boutons ne serait pas lisible (contraste insuffisant)."
            )
    return out


def _check_choices(field: dict, path: str) -> dict[str, str]:
    options, source = field.get("options"), field.get("source")
    if (options is None) == (source is None):
        return {f"{path}.options": "Indiquez soit des options, soit une source de données."}
    if source is not None:
        if not isinstance(source, dict) or set(source) - SOURCE_KEYS:
            return {f"{path}.source": "Source de données invalide."}
        for name in ("connection", "path", "items_path", "label_path", "value_path"):
            if not _text(source.get(name, ""), 300):
                return {f"{path}.source.{name}": "Texte trop long (300 caractères)."}
        params = source.get("params", [])
        if not isinstance(params, list) or len(params) > 5 or not all(_text(p, 64) for p in params):
            return {f"{path}.source.params": "Cinq paramètres au plus."}
        return shaping.check(source, f"{path}.source")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        return {f"{path}.options": f"Entre 1 et {MAX_OPTIONS} options."}
    values = set()
    for i, opt in enumerate(options):
        if not isinstance(opt, dict) or set(opt) - {"value", "label"} or not _text(opt.get("value"), 200):
            return {f"{path}.options.{i}": "Option invalide (valeur et libellé en texte)."}
        if not opt["value"] or opt["value"] in values or not _text(opt.get("label", opt["value"]), 200):
            return {f"{path}.options.{i}": "Valeur vide, en double ou libellé trop long."}
        values.add(opt["value"])
    return {}


def _check_columns(field: dict, path: str) -> dict[str, str]:
    columns = field.get("columns")
    if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_COLUMNS:
        return {f"{path}.columns": f"Entre 1 et {MAX_COLUMNS} colonnes."}
    keys = set()
    for i, col in enumerate(columns):
        ok = isinstance(col, dict) and set(col) <= {"key", "label", "type"}
        if not ok or not KEY_RE.match(str(col.get("key"))) or col["key"] in keys:
            return {f"{path}.columns.{i}": "Colonne invalide (clé unique)."}
        if col.get("type", "text") not in ("text", "number") or not _text(col.get("label", ""), 160):
            return {f"{path}.columns.{i}": "Type « text » ou « number », libellé de 160 caractères au plus."}
        keys.add(col["key"])
    return {}


def _check_rule(rule, path: str, others: list[str]) -> dict[str, str]:
    if not isinstance(rule, str):
        return {path: "Une expression est attendue."}
    try:
        ast = parse(rule)
    except ExpressionError as exc:
        return {path: f"Règle invalide : {exc}"}
    for p in paths_in(ast):
        if len(p) < 2 or p[0] != "values":
            return {path: "Une règle ne peut lire que les champs du formulaire (values.<clé>)."}
        if p[1] not in others:
            return {path: f"« {p[1]} » doit être un autre champ du formulaire."}
    return {}


def _json_type(value) -> str:
    return {bool: "boolean", int: "number", float: "number", str: "string", list: "array", dict: "object"}.get(
        type(value), "null"
    )


def _valid_map(mapping) -> bool:
    """`{workflow input: key of a field of the form}`; an empty text means that no field feeds the input."""
    return (
        isinstance(mapping, dict)
        and len(mapping) <= 200
        and all(
            isinstance(k, str) and KEY_RE.match(k) and isinstance(v, str) and (v == "" or KEY_RE.match(v))
            for k, v in mapping.items()
        )
    )


def input_source(mapping: dict | None, name: str, keys) -> str | None:
    """The key of the form field that feeds the workflow input `name`: the one chosen in the correspondence, else the field of
    the same name, else none."""
    mapping = mapping or {}
    if name in mapping:
        return mapping[name] or None
    return name if name in keys else None


def _wanted_type(declared):
    """The type(s) a workflow input accepts, « null » left out: a field left empty is omitted, which is what null means for an optional input."""
    if not isinstance(declared, list):
        return declared
    kept = [t for t in declared if t != "null"] or None
    return kept[0] if kept and len(kept) == 1 else kept


def _type_fits(want, have: str) -> bool:
    accepted = {want} if isinstance(want, str) else set(want or [have])
    return have in accepted or ("integer" in accepted and have == "number")


def check_against_inputs(definition: dict, input_schema: dict, mapping: dict | None = None) -> dict[str, str]:
    """The link with the workflow: every input is fed by a field of compatible type (the field of the same name, or the one chosen
    in the correspondence `mapping`); every required input is asked for, and never behind a visibility rule (a hidden required
    input would make every submission fail). Without any correspondence, every field must also be an input of the workflow."""
    props = (input_schema or {}).get("properties", {})
    required = set((input_schema or {}).get("required", []))
    mapping = mapping or {}
    fields = {f["key"]: f for f in data_fields(definition)}
    problems: dict[str, str] = {}
    if not mapping:
        for key in fields:
            if key not in props:
                problems[key] = f"Le workflow n'a pas d'entrée « {key} »."
    else:
        for name in mapping:
            if name not in props:
                problems[name] = f"Le workflow n'a pas d'entrée « {name} »."
    for name, prop in props.items():
        if name in problems:
            continue
        key = input_source(mapping, name, fields)
        if key is None:
            if name in required and "default" not in prop:
                problems[name] = f"L'entrée obligatoire « {name} » du workflow n'est demandée par aucun champ."
            continue
        field = fields.get(key)
        if field is None:
            problems[name] = f"Le champ « {key} » n'existe pas dans le formulaire."
            continue
        have = (
            formcontrols.produces(field["control"]["spec"]) if field["type"] == "custom" else FIELD_TYPES[field["type"]]
        )
        want = _wanted_type(prop.get("type"))
        if not _type_fits(want, have):
            problems[name] = f"Le workflow attend « {want} » ; le champ « {key} » produit « {have} »."
        if name in required and (field.get("visible_if") or formrules.can_hide(field)):
            problems[name] = "Une entrée obligatoire du workflow ne peut pas être masquée par une règle."
    return problems


def input_fields(definition: dict, input_schema: dict, mapping: dict | None) -> dict[str, str]:
    """{workflow input: key of the field that feeds it}, to give an input's error back to the field the person sees."""
    keys = {f["key"] for f in data_fields(definition)}
    found = {name: input_source(mapping, name, keys) for name in (input_schema or {}).get("properties", {})}
    return {name: key for name, key in found.items() if key}


def map_inputs(definition: dict, input_schema: dict, mapping: dict | None, values: dict) -> dict:
    """The inputs of the workflow from the values of the form (by field key). Without a correspondence the values are the inputs."""
    if not mapping:
        return values
    props = (input_schema or {}).get("properties", {})
    keys = {f["key"] for f in data_fields(definition)}
    out = {}
    for name in props:
        key = input_source(mapping, name, keys)
        if key is not None and key in values:
            out[name] = values[key]
    return out


def _is_visible(field: dict, values: dict, known: list[str]) -> bool:
    rule = field.get("visible_if")
    if not rule:
        return True
    ctx = {"values": {k: values.get(k) for k in known}}
    try:
        return bool(evaluate_ast(parse(rule), ctx))
    except ExpressionError:
        return False  # an unreadable situation (a missing value compared to a number…) hides the field


MAX_PASSES = (
    8  # same number in the browser (`forms/logic.ts`): fields that read each other settle in a few passes, or stop here
)


def _pass(ordered: list[dict], values: dict, known: list[str], kept: dict) -> list[tuple[dict, dict]]:
    """One evaluation of every field in order. `kept` holds what the previous pass kept: a field read before its own turn (a loop) is seen
    as that pass left it; every field is recomputed, so one that is now hidden drops its value."""
    shown: list[tuple[dict, dict]] = []
    for field in ordered:
        own = {field["key"]: values[field["key"]]} if field["key"] in values else {}
        state = formrules.apply_rules(
            field, {**kept, **own}, formrules.initial_state(field)
        )  # a rule may read the field's own typed value
        kept.pop(field["key"], None)
        if state["visible"] and _is_visible(field, kept, known):
            shown.append((field, state))
            if state["fill"] is not None:  # a rule fills the field: its value is the rule's, whatever was sent
                kept[field["key"]] = formrules.fill_value(field, state["fill"])
            elif "compute" in field:  # a calculated field: the server's result, never what was sent
                value = formcalc.value_for(field["type"], formcalc.evaluate(field["compute"], kept))
                if value is not None:
                    kept[field["key"]] = value
            elif field["key"] in values:
                kept[field["key"]] = values[field["key"]]
                if field["type"] == "custom":  # the control's own rules: hidden and calculated sub-values, blank lines
                    normal, problem = formcontrols.process(field, values[field["key"]])
                    if not problem:  # otherwise keep what was sent: the validation below reports the problem
                        kept[field["key"]] = normal
    return shown


def field_states(definition: dict, values: dict) -> tuple[dict, list[tuple[dict, dict]]]:
    """Values of the visible fields only (a hidden field's value is dropped, never forwarded) and, for each visible field, its state
    once its rules have been applied: `required`, accepted `options`… Conditions read the values kept so far; fields that read each
    other (a loop) are evaluated again until the result no longer changes (at most MAX_PASSES times)."""
    kept: dict = {}
    fields = list(data_fields(definition))
    known = [f["key"] for f in fields]
    rank = {f["key"]: i for i, f in enumerate(fields)}
    ordered = evaluation_order(fields)
    shown: list[tuple[dict, dict]] = []
    for _ in range(MAX_PASSES):
        before = (dict(kept), [f["key"] for f, _ in shown])
        shown = _pass(ordered, values, known, kept)
        if (kept, [f["key"] for f, _ in shown]) == before:
            break
    shown.sort(key=lambda fs: rank[fs[0]["key"]])
    return kept, shown


def visible_values(definition: dict, values: dict) -> tuple[dict, list[dict]]:
    kept, shown = field_states(definition, values)
    return kept, [f for f, _ in shown]


def _text_problem(field: dict, value) -> str:
    if not isinstance(value, str):
        return "Un texte est attendu."
    if len(value) > (MAX_LONG_TEXT if field["type"] == "textarea" else MAX_TEXT):
        return "Texte trop long."
    return ""


def _email_problem(field: dict, value) -> str:
    return _text_problem(field, value) or ("" if EMAIL_RE.match(value) else "Adresse e-mail invalide.")


def _date_problem(field: dict, value) -> str:
    problem = _text_problem(field, value)
    if problem:
        return problem
    try:
        date.fromisoformat(value)
    except ValueError:
        return "Date invalide (AAAA-MM-JJ)."
    return ""


def _number_problem(field: dict, value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "Un nombre est attendu."
    if field.get("min") is not None and value < field["min"]:
        return f"Au moins {field['min']}."
    if field.get("max") is not None and value > field["max"]:
        return f"Au plus {field['max']}."
    return ""


def _checkbox_problem(field: dict, value) -> str:
    return "" if isinstance(value, bool) else "Vrai ou faux attendu."


def _checkbox_list_problem(field: dict, value) -> str:
    if not isinstance(value, list) or len(value) > MAX_OPTIONS or not all(_text(p, 200) for p in value):
        return "Une liste de textes est attendue."
    return ""


_KIND_PROBLEMS = {
    **dict.fromkeys(("text", "textarea", "select", "radio", "combobox"), _text_problem),
    "email": _email_problem,
    "date": _date_problem,
    "number": _number_problem,
    "checkbox": _checkbox_problem,
    "checkbox_list": _checkbox_list_problem,
}


def _field_problem(field: dict, value) -> str:
    kind = field["type"]
    check = _KIND_PROBLEMS.get(kind)
    problem = check(field, value) if check else ""
    if problem:
        return problem
    if kind in CHOICE_TYPES and "options" in field:
        allowed = {o["value"] for o in field["options"]}
        if any(p not in allowed for p in (value if isinstance(value, list) else [value])):
            return "Choix non proposé."
    if kind == "datagrid":
        return _grid_problem(field, value)
    if kind == "custom":
        return formcontrols.process(field, value)[1]
    return ""


def _grid_problem(field: dict, value) -> str:
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        return f"Une liste de {MAX_ROWS} lignes au plus est attendue."
    cols = {c["key"]: c.get("type", "text") for c in field["columns"]}
    for row in value:
        if not isinstance(row, dict) or set(row) - set(cols):
            return "Ligne invalide."
        for k, v in row.items():
            numeric = cols[k] == "number"
            if numeric and (isinstance(v, bool) or not isinstance(v, (int, float))):
                return "Un nombre est attendu dans les colonnes numériques."
            if not numeric and not _text(v, MAX_TEXT):
                return "Texte trop long dans une cellule."
    return ""


def validate_submission(definition: dict, values: dict) -> tuple[dict, dict[str, str]]:
    """Returns (the values to hand to the workflow, {key: message}). Unknown and hidden keys never get through."""
    kept, shown = field_states(definition, values)
    errors: dict[str, str] = {}
    for field, state in shown:
        key = field["key"]
        value = kept.get(key)
        empty = value is None or value == "" or value == [] or value == {}
        if empty:
            kept.pop(key, None)
            if state["required"]:
                errors[key] = "Ce champ est obligatoire."
            continue
        problem = _field_problem(field, value)
        if not problem and state["options"] is not None:
            picked = value if isinstance(value, list) else [value]
            if any(formrules.text_of(p) not in state["options"] for p in picked):
                problem = "Choix non proposé."
        if problem:
            errors[key] = problem
    if definition.get(
        "submit"
    ):  # un bouton masqué ou bloqué par ses conditions : un envoi forgé hors de l'interface est refusé aussi
        state = submit_state(definition, kept)
        if not state["visible"] or state["readonly"]:
            errors["_"] = state["help"] or "L'envoi n'est pas possible : une condition du formulaire n'est pas remplie."
    return kept, errors
