"""Rules of a form field: conditions on the values of earlier fields, and what they change.

A field may carry `rules`. A rule is `when` (a condition tree), `then` (effects applied when it holds) and optionally `else`
(effects applied when it does not). Rules are applied in order; a later effect overrides an earlier one.

    {"when": {"op": "all", "items": [{"field": "pays", "op": "equals", "value": "FR", "ci": true},
                                     {"op": "any", "items": [{"field": "age", "op": "gte", "value": 18}, ...]}]},
     "then": [{"do": "show"}, {"do": "require"}, {"do": "text", "target": "label", "text": "Département de {pays}"}],
     "else": [{"do": "hide"}]}

Conditions compare the value of an earlier field with a constant (`value`), with another earlier field (`ref`) or with a list
(`values`). Both this module and the browser (`frontend/src/forms/rules.ts`) evaluate them with the very same definitions,
checked on one shared list of cases (`tests/fixtures/form_rules_cases.json`). What decides is the server: hidden values are
dropped, `require` makes a field obligatory, `options` limits the accepted choices."""

import re

MAX_RULES = 10
MAX_DEPTH = 4
MAX_CONDITIONS = 30
MAX_EFFECTS = 10
MAX_LIST = 50
MAX_TEXT = 500

UNARY = {"is_empty", "not_empty", "is_true", "is_false"}
LISTED = {"in_list", "includes_any"}
BINARY = {"equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "gt", "gte", "lt", "lte"}
OPERATORS = UNARY | LISTED | BINARY
EFFECTS = {"show", "hide", "require", "optional", "text", "options", "fill", "load"}
LOADS = ("options", "prefill")  # what a « load » effect lets a custom control read from its service
FILLABLE = {"text", "textarea", "email", "date", "number", "select", "radio", "combobox", "checkbox"}
TEXT_TARGETS = {"label", "help", "placeholder"}
TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
_DECIMAL = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHOICE = {"select", "radio", "combobox", "checkbox_list"}
_BLOCKS = {"heading", "paragraph", "divider"}


# ---- evaluation ------------------------------------------------------------------------------------------------------
def text_of(v) -> str:
    """A value as text, the same way in the browser: numbers without a useless « .0 », booleans as true/false, lists joined by commas."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join(text_of(x) for x in v)
    return ""


def num_of(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _DECIMAL.match(v.strip()):
        return float(v.strip())
    return None


def _empty(v) -> bool:
    return v is None or v == "" or v == []


def eval_cond(cond: dict, values: dict) -> bool:  # noqa: C901
    # the left operand: one field, or a composed value (« {prenom} {nom} »)
    left = render(cond["template"], values) if "template" in cond else values.get(cond["field"])
    op = cond["op"]
    fold = (lambda s: s.lower()) if cond.get("ci") is True else (lambda s: s)
    if op == "is_empty":
        return _empty(left)
    if op == "not_empty":
        return not _empty(left)
    if op == "is_true":
        return left is True
    if op == "is_false":
        return left is False
    if op in LISTED:
        needles = [fold(text_of(x)) for x in cond.get("values", [])]
        if op == "in_list":
            return fold(text_of(left)) in needles
        return isinstance(left, list) and any(fold(text_of(i)) in needles for i in left)
    right = values.get(cond["ref"]) if "ref" in cond else cond.get("value")
    if op in ("equals", "not_equals"):
        same = fold(text_of(left)) == fold(text_of(right))
        return same if op == "equals" else not same
    if op in ("contains", "not_contains"):
        needle = fold(text_of(right))
        found = (
            any(fold(text_of(i)) == needle for i in left) if isinstance(left, list) else needle in fold(text_of(left))
        )
        return found if op == "contains" else not found
    if op == "starts_with":
        return fold(text_of(left)).startswith(fold(text_of(right)))
    if op == "ends_with":
        return fold(text_of(left)).endswith(fold(text_of(right)))
    nl, nr = num_of(left), num_of(right)
    if nl is not None and nr is not None:
        a, b = nl, nr
    elif isinstance(left, str) and isinstance(right, str) and _ISO_DATE.match(left) and _ISO_DATE.match(right):
        a, b = left, right  # ISO dates compare as text
    else:
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def eval_node(node: dict, values: dict) -> bool:
    if "items" in node:
        results = (eval_node(i, values) for i in node["items"])
        return all(results) if node.get("op") == "all" else any(results)
    return eval_cond(node, values)


def render(template: str, values: dict) -> str:
    return TOKEN.sub(lambda m: text_of(values.get(m.group(1))), template)


def initial_state(field: dict) -> dict:
    return {
        "visible": True,
        "required": bool(field.get("required")),
        "label": None,
        "help": None,
        "placeholder": None,
        "options": None,
        "fill": None,
        "load": [],
    }


def fill_value(field: dict, text: str):
    """The value a « fill » effect gives to a field: text as is, a number parsed when it reads as one, a box ticked for
    « true / vrai / oui / 1 ». A text that does not read as a number is left as text: the usual validation then refuses it."""
    kind = field.get("type")
    if kind == "checkbox":
        return text.strip().lower() in ("true", "vrai", "oui", "1")
    if kind == "number":
        try:
            n = float(text.strip().replace(",", "."))
        except ValueError:
            return text
        return int(n) if n.is_integer() and abs(n) < 1e15 else n
    return text


def _apply(effect: dict, values: dict, state: dict) -> None:
    kind = effect["do"]
    if kind in ("show", "hide"):
        state["visible"] = kind == "show"
    elif kind in ("require", "optional"):
        state["required"] = kind == "require"
    elif kind == "text":
        state[effect["target"]] = render(effect["text"], values)
    elif kind == "fill":
        state["fill"] = render(effect["value"], values)
    elif kind == "options":
        state["options"] = [text_of(v) for v in effect["values"]]
    elif kind == "load" and effect["what"] not in state["load"]:
        state["load"].append(effect["what"])


def gated_loads(field: dict) -> set[str]:
    """What the field's rules put under their control: a service reading that a « load » effect names loads only while one of
    these effects applies; a reading no rule names loads by itself."""
    return {
        e["what"]
        for r in field.get("rules") or []
        for b in ("then", "else")
        for e in r.get(b) or []
        if isinstance(e, dict) and e.get("do") == "load"
    }


def apply_rules(field: dict, values: dict, state: dict) -> dict:
    """The state of a field (visible, required, texts, accepted options) once its rules have been applied to `values`."""
    for rule in field.get("rules") or []:
        branch = rule.get("then") if eval_node(rule["when"], values) else rule.get("else")
        for effect in branch or []:
            _apply(effect, values, state)
    return state


def can_hide(field: dict) -> bool:
    return any(
        e.get("do") in ("show", "hide")
        for r in field.get("rules") or []
        for b in ("then", "else")
        for e in r.get(b) or []
    )


# ---- validation of the structure ---------------------------------------------------------------------------------------
def _scalar(v, limit: int = MAX_TEXT) -> bool:
    return (
        (isinstance(v, str) and len(v) <= limit)
        or (isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e15)
        or isinstance(v, bool)
    )


def _check_cond(cond: dict, path: str, earlier: dict[str, dict], count: list[int]) -> dict[str, str]:
    allowed = {"field", "template", "op", "value", "ref", "values", "ci"}
    if set(cond) - allowed:
        return {path: "Condition invalide."}
    count[0] += 1
    if count[0] > MAX_CONDITIONS:
        return {path: f"{MAX_CONDITIONS} conditions au plus par règle."}
    if "template" in cond:
        text = cond["template"]
        if "field" in cond or not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            return {f"{path}.template": f"Valeur composée : un texte de 1 à {MAX_TEXT} caractères, sans champ à côté."}
        if not _tokens_ok(text, earlier):
            return {f"{path}.template": "Un {champ} de la valeur composée doit être un autre champ du formulaire."}
        src: dict = {"type": "text"}
    else:
        src = earlier.get(cond.get("field"))
        if src is None:
            return {f"{path}.field": "Une condition ne peut lire qu'un autre champ du formulaire."}
    op = cond.get("op")
    if op not in OPERATORS:
        return {f"{path}.op": "Opérateur inconnu."}
    if "ci" in cond and not isinstance(cond["ci"], bool):
        return {f"{path}.ci": "Vrai ou faux attendu."}
    kind = src.get("type")
    if kind == "custom" and op not in ("is_empty", "not_empty"):
        return {f"{path}.op": "Un contrôle personnalisé ne se teste que « vide » ou « renseigné »."}
    if op in ("is_true", "is_false") and kind != "checkbox":
        return {f"{path}.op": "« vrai » et « faux » ne s'appliquent qu'à une case à cocher."}
    if op == "includes_any" and (kind != "checkbox_list" or "template" in cond):
        return {f"{path}.op": "« contient l'un de » ne s'applique qu'à des cases multiples."}
    operands = [k for k in ("value", "ref", "values") if k in cond]
    if op in UNARY:
        return {path: "Cet opérateur n'attend aucune valeur."} if operands else {}
    if op in LISTED:
        vals = cond.get("values")
        if (
            operands != ["values"]
            or not isinstance(vals, list)
            or not 1 <= len(vals) <= MAX_LIST
            or not all(_scalar(v, 200) for v in vals)
        ):
            return {f"{path}.values": f"Une liste de 1 à {MAX_LIST} valeurs est attendue."}
        return {}
    if operands not in (["value"], ["ref"]):
        return {path: "Indiquez une valeur, ou un autre champ à comparer."}
    if "value" in cond and not _scalar(cond["value"]):
        return {f"{path}.value": f"Valeur invalide (texte de {MAX_TEXT} caractères au plus, nombre ou vrai/faux)."}
    if "ref" in cond and (cond["ref"] not in earlier or earlier[cond["ref"]].get("type") == "custom"):
        return {
            f"{path}.ref": "Le champ comparé doit être un autre champ du formulaire (pas un contrôle personnalisé)."
        }
    return {}


def _check_node(node, path: str, earlier: dict[str, dict], depth: int, count: list[int]) -> dict[str, str]:
    if not isinstance(node, dict):
        return {path: "Condition invalide."}
    if "items" in node:
        if set(node) != {"op", "items"} or node["op"] not in ("all", "any") or not isinstance(node["items"], list):
            return {path: "Groupe invalide : {op: all|any, items: [...]}."}
        if depth > MAX_DEPTH:
            return {path: f"{MAX_DEPTH} niveaux de groupes au plus."}
        if not node["items"]:
            return {path: "Un groupe contient au moins une condition."}
        out: dict[str, str] = {}
        for i, item in enumerate(node["items"]):
            out.update(_check_node(item, f"{path}.items.{i}", earlier, depth + 1, count))
        return out
    return _check_cond(node, path, earlier, count)


def _tokens_ok(text: str, earlier: dict[str, dict]) -> bool:
    return all(t in earlier for t in TOKEN.findall(text))


def _check_effect(effect, path: str, field: dict, earlier: dict[str, dict]) -> dict[str, str]:  # noqa: C901
    if not isinstance(effect, dict) or effect.get("do") not in EFFECTS:
        return {path: "Effet inconnu."}
    kind = effect["do"]
    is_block = field.get("type") in _BLOCKS
    if kind in ("show", "hide"):
        return {path: "Cet effet n'a pas de paramètre."} if set(effect) != {"do"} else {}
    if kind in ("require", "optional"):
        if is_block:
            return {path: "Un bloc de contenu ne peut pas être obligatoire."}
        return {path: "Cet effet n'a pas de paramètre."} if set(effect) != {"do"} else {}
    if kind == "text":
        if set(effect) != {"do", "target", "text"} or effect["target"] not in TEXT_TARGETS:
            return {path: "Texte : {target: label|help|placeholder, text}."}
        if is_block and effect["target"] != "label":
            return {path: "Un bloc de contenu n'a que son texte."}
        text = effect["text"]
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            return {f"{path}.text": f"Texte de {MAX_TEXT} caractères au plus."}
        if not _tokens_ok(text, earlier):
            return {f"{path}.text": "Un {champ} du texte doit être un autre champ du formulaire."}
        return {}
    if kind == "fill":
        if set(effect) != {"do", "value"} or not isinstance(effect["value"], str) or len(effect["value"]) > MAX_TEXT:
            return {path: f"Remplir : {{value}}, texte de {MAX_TEXT} caractères au plus."}
        if field.get("type") not in FILLABLE:
            return {path: "Ce type de champ ne peut pas être rempli par une règle."}
        if not _tokens_ok(effect["value"], earlier):
            return {f"{path}.value": "Un {champ} de la valeur doit être un autre champ du formulaire."}
        return {}
    if kind == "load":
        if set(effect) != {"do", "what"} or effect["what"] not in LOADS:
            return {path: "Charger : {what: options|prefill}."}
        has = "source" if effect["what"] == "options" else "prefill"
        if field.get("type") != "custom" or has not in field:
            return {path: "Ce champ ne lit pas ce service : réglez d'abord « Données depuis un service » du champ."}
        return {}
    # options
    if set(effect) != {"do", "values"} or not isinstance(effect["values"], list):
        return {path: "Choix : {values: [...]}."}
    if field.get("type") not in _CHOICE or not isinstance(field.get("options"), list):
        return {path: "Limiter les choix ne concerne qu'une liste dont les options sont saisies dans le formulaire."}
    known = {o.get("value") for o in field["options"] if isinstance(o, dict)}
    if not all(isinstance(v, str) and v in known for v in effect["values"]) or len(effect["values"]) > MAX_LIST:
        return {f"{path}.values": "Ces valeurs doivent faire partie des options du champ."}
    return {}


def _nodes(node):
    if isinstance(node, dict):
        if "items" in node:
            for item in node["items"] if isinstance(node["items"], list) else []:
                yield from _nodes(item)
        else:
            yield node


def reads(field: dict) -> set[str]:
    """Keys of the fields whose value the rules of this field read (conditions, compared fields, {tokens})."""
    out: set[str] = set()
    for rule in field.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        for cond in _nodes(rule.get("when")):
            out.update(k for k in (cond.get("field"), cond.get("ref")) if isinstance(k, str))
            if isinstance(cond.get("template"), str):
                out.update(TOKEN.findall(cond["template"]))
        for b in ("then", "else"):
            for e in rule.get(b) or []:
                if isinstance(e, dict):
                    for k in ("text", "value"):
                        if isinstance(e.get(k), str):
                            out.update(TOKEN.findall(e[k]))
    return out


def check_rules(rules, path: str, field: dict, earlier: dict[str, dict]) -> dict[str, str]:
    """Structure of the rules of one field. `earlier`: the other data fields of the form (anywhere), by key."""
    if rules is None:
        return {}
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        return {path: f"{MAX_RULES} règles au plus par champ."}
    out: dict[str, str] = {}
    for i, rule in enumerate(rules):
        at = f"{path}.{i}"
        if not isinstance(rule, dict) or not {"when"} <= set(rule) <= {"id", "name", "when", "then", "else"}:
            out[at] = "Règle invalide : {when, then, else} attendus."
            continue
        if "name" in rule and not (isinstance(rule["name"], str) and len(rule["name"]) <= 120):
            out[f"{at}.name"] = "Nom de 120 caractères au plus."
        if "id" in rule and not (isinstance(rule["id"], str) and len(rule["id"]) <= 40):
            out[f"{at}.id"] = "Identifiant invalide."
        out.update(_check_node(rule["when"], f"{at}.when", earlier, 1, [0]))
        branches = [b for b in ("then", "else") if b in rule]
        if not rule.get("then"):
            out[f"{at}.then"] = "Une règle a au moins un effet."
        for b in branches:
            effects = rule[b]
            if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
                out[f"{at}.{b}"] = f"{MAX_EFFECTS} effets au plus."
                continue
            for j, e in enumerate(effects):
                out.update(_check_effect(e, f"{at}.{b}.{j}", field, earlier))
    return out
