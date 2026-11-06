"""Custom controls: a field of a form made of other fields.

A control is described by a *spec* (`formbuilder.Control`, published in versions) and a form field of type `custom` keeps a copy of the
spec it uses, so a form is self-contained: it is validated, published, exported and served without looking anything up.

    mode "group"   one set of sub-fields; the value is an object                     (an address, a person, a date range…)
    mode "repeat"  the same set of sub-fields repeated; the value is a list of objects (invoice lines, guests…)
    mode "items"   a list of boxes to tick, each opening optional details; the value is a list (of the chosen values, or
                   of objects `{value, …details}` when the control has details)
    mode "choice"  one choice among a list (a drop-down or radio buttons), each option possibly opening its own details; the
                   value is the chosen value, or an object `{value, …details}` when there are details

Details of a choice are the control's shared `parts` followed by the `parts` of the option itself, so an option may bring a list
of boxes, another drop-down or a whole nested control. A control never carries a call to a web service (it is reused by many forms, which each have their own): it may only declare
`dynamic` (its options are supplied by the form that uses it), and the FORM FIELD then holds the reading of the service: `source`
(the options) and, for any control, `prefill` (the whole value), both through a connection of the workspace, never from the browser.
Options may be put in `group`s (a heading above consecutive options) and `disabled` (shown, not selectable); a list of boxes or a
choice may be shown as a searchable drop-down (`display: "dropdown"`). A repeated control may be shown as a table (`display: "table"`, one column
per sub-field, a drop-down per line…) with totals under chosen numeric columns. Controls nest up to MAX_DEPTH levels.

The sub-fields (`parts`) are ordinary form fields laid out on the 12-column grid, with their own required/min/max, options,
conditional rules and calculated values: one line of a control is validated *as a small form* by `formengine`, so everything
a form field can do, a part can do (nested controls excepted).
"""

import re

from . import shaping

MODES = ("group", "repeat", "items", "choice")
PART_TYPES = {"text", "textarea", "number", "email", "date", "select", "radio", "checkbox", "checkbox_list", "custom"}
BLOCK_PARTS = {"heading", "paragraph", "divider"}
MAX_PARTS, MAX_ROWS, MAX_ITEMS, MAX_DEPTH = 30, 50, 100, 3
SPEC_KEYS = {
    "version",
    "mode",
    "parts",
    "items",
    "items_editable",
    "min",
    "max",
    "row_label",
    "add_label",
    "display",
    "totals",
    "placeholder",
    "dynamic",
}
ITEM_KEYS = {"value", "label", "parts", "group", "disabled"}
SOURCE_KEYS = {
    "connection",
    "path",
    "items_path",
    "label_path",
    "value_path",
    "group_path",
    "disabled_path",
    "params",
} | shaping.SHAPING_KEYS
PREFILL_KEYS = {"connection", "path", "value_path", "params"}
DISPLAYS = {"repeat": ("cards", "table"), "choice": ("select", "radio", "dropdown"), "items": ("list", "dropdown")}
CONTROL_KEYS = {"ref", "version", "name", "icon", "spec"}
REF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
ICON_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")


def row_definition(parts: list) -> dict:
    """The parts of one line, as the small form the engine validates."""
    return {"version": 1, "sections": [{"id": "row", "title": "Ligne", "fields": parts}]}


def has_details(spec: dict) -> bool:
    """Whether a choice or a box opens details: shared sub-fields, or sub-fields of one of the options."""
    sub = [*spec.get("parts", []), *(p for o in spec.get("items", []) for p in o.get("parts", []))]
    return any(p.get("type") not in BLOCK_PARTS for p in sub)


def produces(spec: dict) -> str:
    """The JSON type the control hands to the workflow."""
    mode = spec.get("mode")
    if mode == "choice":
        return "object" if has_details(spec) else "string"
    return "object" if mode == "group" else "array"


def depth(spec, limit: int = MAX_DEPTH + 2) -> int:
    """Levels of controls below (and including) this spec; stops counting past `limit`."""
    if not isinstance(spec, dict) or limit <= 0:
        return 0
    subs = list(spec.get("parts", [])) if isinstance(spec.get("parts"), list) else []
    if isinstance(spec.get("items"), list):
        subs += [
            p for o in spec["items"] if isinstance(o, dict) and isinstance(o.get("parts"), list) for p in o["parts"]
        ]
    below = [
        depth(p["control"].get("spec"), limit - 1)
        for p in subs
        if isinstance(p, dict) and isinstance(p.get("control"), dict)
    ]
    return 1 + max(below, default=0)


def _short(text, limit: int) -> bool:
    return isinstance(text, str) and len(text) <= limit


def _check_parts(parts, mode: str, path: str, *, key_reserved: bool) -> dict[str, str]:
    """Each sub-field is an ordinary field of a simple type, or a nested control (never a dynamic list or a table)."""
    out: dict[str, str] = {}
    if not isinstance(parts, list) or len(parts) > MAX_PARTS:
        return {path: f"{MAX_PARTS} sous-champs au plus."}
    for i, part in enumerate(parts):
        kind = part.get("type") if isinstance(part, dict) else None
        if not isinstance(part, dict) or (kind not in PART_TYPES and kind not in BLOCK_PARTS):
            out[f"{path}.{i}"] = "Type de sous-champ non permis (pas de liste dynamique ni de tableau)."
        elif "source" in part or "prefill" in part:
            out[f"{path}.{i}"] = (
                "Un sous-champ ne lit pas de service : seul le champ de premier niveau du formulaire le peut."
            )
        elif key_reserved and part.get("key") == "value":
            out[f"{path}.{i}.key"] = "« value » est réservé au choix de la liste."
    return out


def _check_service(cfg, keys: set, path: str) -> dict[str, str]:
    """A reading of a web service: a connection, a path (placeholders = fields of the form) and where to read in the reply."""
    if not isinstance(cfg, dict) or set(cfg) - keys:
        return {path: "Lecture d'un service invalide."}
    for name in sorted(keys - {"params"}):
        if not _short(cfg.get(name, ""), 300):
            return {f"{path}.{name}": "Texte de 300 caractères au plus."}
    if not cfg.get("connection") or not str(cfg.get("path", "")).startswith("/"):
        return {path: "Indiquez la connexion et un chemin qui commence par « / »."}
    params = cfg.get("params", [])
    if not isinstance(params, list) or len(params) > 5 or not all(_short(p, 64) and p for p in params):
        return {f"{path}.params": "Cinq paramètres au plus (noms de champs du formulaire)."}
    return shaping.check(cfg, path) if "items_path" in keys else {}


def _check_items(spec: dict, mode: str, path: str, draft: bool) -> dict[str, str]:
    """The boxes or the options of a list: value, label and, facultatively, the sub-fields this option opens."""
    from . import formengine

    items = spec.get("items", [])
    if not isinstance(items, list):
        return {f"{path}.items": "Une liste de choix est attendue."}
    if len(items) > MAX_ITEMS:
        return {f"{path}.items": f"{MAX_ITEMS} choix au plus."}
    out: dict[str, str] = {}
    editable = spec.get("items_editable")
    if "items_editable" in spec and (mode != "items" or not isinstance(editable, bool)):
        out[f"{path}.items_editable"] = "Vrai ou faux attendu (liste à cocher seulement)."
    plain = [{k: v for k, v in o.items() if k in ("value", "label")} if isinstance(o, dict) else o for o in items]
    if items or not (editable or draft or spec.get("dynamic")):
        out.update(formengine._check_choices({"options": plain}, f"{path}.items"))
    if out:
        return out
    shared = spec.get("parts", [])
    for i, o in enumerate(items):
        if set(o) - ITEM_KEYS:
            return {f"{path}.items.{i}": "Option invalide (valeur, libellé, groupe, désactivée et sous-champs)."}
        if not _short(o.get("group", ""), 120) or not isinstance(o.get("disabled", False), bool):
            return {f"{path}.items.{i}": "Groupe de 120 caractères au plus ; « désactivée » : vrai ou faux."}
        if "parts" not in o:
            continue
        at = f"{path}.items.{i}.parts"
        if editable:
            return {at: "Une liste que le concepteur du formulaire modifie n'a pas de détails propres à chaque choix."}
        bad = _check_parts(o["parts"], mode, at, key_reserved=True)
        if not bad:
            keys = [p.get("key") for p in [*shared, *o["parts"]]]
            if len(set(keys)) != len(keys):
                bad = {at: "Une clé est déjà utilisée par les sous-champs communs."}
        if not bad:
            for key, message in formengine.check_definition(row_definition([*shared, *o["parts"]])).items():
                m = re.match(r"^sections\.0\.fields\.(\d+)(.*)$", key)
                if m and int(m.group(1)) >= len(shared):
                    bad[f"{at}.{int(m.group(1)) - len(shared)}{m.group(2)}"] = message
        if bad:
            return bad
    return out


def check_spec(spec, path: str = "spec", *, draft: bool = False) -> dict[str, str]:  # noqa: C901
    """Structure of a control spec; {path: message}. A draft may still be empty (no sub-field yet): publishing is stricter."""
    from . import formengine

    if not isinstance(spec, dict) or set(spec) - SPEC_KEYS:
        return {path: "Contrôle invalide."}
    if spec.get("version") != 1:
        return {f"{path}.version": "Version de description : 1."}
    mode = spec.get("mode")
    if mode not in MODES:
        return {f"{path}.mode": "Mode : group, repeat, items ou choice."}
    if depth(spec) > MAX_DEPTH:
        return {path: f"Des contrôles imbriqués sur {MAX_DEPTH} niveaux au plus."}
    parts = spec.get("parts", [])
    out = _check_parts(parts, mode, f"{path}.parts", key_reserved=mode in ("items", "choice"))
    if out:
        return out
    data = [p for p in parts if p.get("type") not in BLOCK_PARTS]
    if mode not in ("items", "choice") and not data and not draft:
        return {f"{path}.parts": "Ajoutez au moins un sous-champ."}
    for key, message in formengine.check_definition(row_definition(parts)).items():
        m = re.match(r"^sections\.0\.fields\.(\d+)(.*)$", key)
        out[f"{path}.parts.{m.group(1)}{m.group(2)}" if m else path] = message
    if out:
        return out
    if mode in ("items", "choice"):
        out.update(_check_items(spec, mode, path, draft))
        if out:
            return out
    elif "items" in spec or "items_editable" in spec:
        out[f"{path}.items"] = "Les choix ne concernent que la liste à cocher et le choix."
    if "dynamic" in spec and (
        not isinstance(spec["dynamic"], bool) or mode not in ("items", "choice") or spec.get("items_editable")
    ):
        out[f"{path}.dynamic"] = (
            "Options fournies par le formulaire : liste à cocher ou choix, dont la liste n'est pas modifiable."
        )
    if "display" in spec and spec["display"] not in DISPLAYS.get(mode, ()):
        out[f"{path}.display"] = "Affichage non proposé pour ce type de contrôle."
    if "placeholder" in spec and (mode not in ("items", "choice") or not _short(spec["placeholder"], 120)):
        out[f"{path}.placeholder"] = "Texte d'invite de 120 caractères au plus (liste à cocher et choix)."
    if "totals" in spec:
        numeric = {p["key"] for p in data if p.get("type") == "number"}
        totals = spec["totals"]
        if (
            mode != "repeat"
            or spec.get("display") != "table"
            or not isinstance(totals, list)
            or not all(isinstance(k, str) and k in numeric for k in totals)
            or len(set(totals)) != len(totals)
        ):
            out[f"{path}.totals"] = "Totaux : colonnes numériques d'un tableau de lignes répétées."
    cap = MAX_ROWS if mode == "repeat" else MAX_ITEMS
    if mode in ("group", "choice"):
        if "min" in spec or "max" in spec:
            out[f"{path}.min"] = "Ce contrôle n'a ni minimum ni maximum."
    else:
        lo, hi = spec.get("min", 0), spec.get("max", cap)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (lo, hi)) or not 0 <= lo <= hi <= cap:
            out[f"{path}.min"] = f"Minimum et maximum : entiers, 0 ≤ minimum ≤ maximum ≤ {cap}."
    for name in ("row_label", "add_label"):
        if name in spec and (mode != "repeat" or not _short(spec[name], 60)):
            out[f"{path}.{name}"] = "Texte de 60 caractères au plus (lignes répétées seulement)."
    return out


def check_field(field: dict, path: str) -> dict[str, str]:
    """The `control` of a form field of type custom: which control (and version) it is, and the spec copied from it."""
    c = field.get("control")
    if not isinstance(c, dict) or set(c) - CONTROL_KEYS or "spec" not in c:
        return {f"{path}.control": "Un contrôle personnalisé porte sa description (control.spec)."}
    if (
        not isinstance(c.get("ref"), str)
        or not REF_RE.match(c["ref"])
        or not VERSION_RE.match(str(c.get("version", "")))
    ):
        return {f"{path}.control": "Référence du contrôle (identifiant et version x.y.z) invalide."}
    icon = c.get("icon", "custom")
    if not _short(c.get("name", ""), 160) or not (isinstance(icon, str) and ICON_RE.match(icon)):
        return {f"{path}.control": "Nom (160 caractères au plus) ou icône invalide."}
    out = check_spec(c["spec"], f"{path}.control.spec")
    spec = c["spec"] if isinstance(c["spec"], dict) else {}
    if not out and "options" in field and not (spec.get("mode") == "items" and spec.get("items_editable")):
        out[f"{path}.options"] = "Ce contrôle n'a pas de choix modifiables."
    if not out and "source" in field:
        if not spec.get("dynamic"):
            out[f"{path}.source"] = "Ce contrôle ne reçoit pas ses options d'un service."
        else:
            out.update(_check_service(field["source"], SOURCE_KEYS, f"{path}.source"))
    if not out and "prefill" in field:
        out.update(_check_service(field["prefill"], PREFILL_KEYS, f"{path}.prefill"))
    return out


def items_of(field: dict) -> list[dict]:
    """The boxes of an « items » control: the field's own list when the control lets the form author edit it."""
    spec = field["control"]["spec"]
    return field["options"] if spec.get("items_editable") and "options" in field else spec.get("items", [])


def _row(rdef: dict, labels: dict, value, prefix: str) -> tuple[dict | None, str]:
    from . import formengine

    if not isinstance(value, dict):
        return None, f"{prefix}Un objet est attendu."
    kept, errors = formengine.validate_submission(rdef, value)
    for key, message in errors.items():
        return kept, f"{prefix}{labels.get(key, key)} : {message}"
    return kept, ""


def _details(spec: dict, item: dict | None) -> tuple[dict, dict]:
    """The small form an option opens (the shared sub-fields, then its own) and the labels of its fields."""
    parts = [*spec.get("parts", []), *((item or {}).get("parts", []))]
    return row_definition(parts), {
        p["key"]: p.get("label") or p["key"] for p in parts if p.get("type") not in BLOCK_PARTS
    }


def _known(items: dict, field: dict, v) -> dict | None:
    """The option a value stands for. When the form field reads its options from a service, a value the fixed list does not know
    is taken as a dynamic option (the server never calls the service while a form is being submitted: only its shape is checked)."""
    if not isinstance(v, str):
        return None
    if v in items:
        return items[v]
    return {} if "source" in field and 0 < len(v) <= 200 else None


def _choice(field: dict, spec: dict, value):
    """One choice: the value alone, or `{value, …details}` when the control (or the option) has details."""
    items = {o["value"]: o for o in items_of(field)}
    raw = {"value": value} if isinstance(value, str) else value
    if not isinstance(raw, dict):
        return value, "Un choix est attendu."
    v = raw.get("value")
    if v in (None, ""):
        return {}, "Ce champ est obligatoire." if field.get("required") else ""
    item = _known(items, field, v)
    if item is None:
        return value, "Choix non proposé."
    if item.get("disabled"):
        return value, "Choix indisponible."
    if not has_details(spec):
        return v, ""
    rdef, labels = _details(spec, item)
    kept, problem = _row(rdef, labels, {k: x for k, x in raw.items() if k != "value"}, f"{item.get('label') or v} — ")
    return ({"value": v, **(kept or {})} if not problem else value), problem


def process(field: dict, value):  # noqa: C901
    """(the value as the server keeps it, a message if it is not acceptable). Hidden and unknown sub-values are dropped, calculated
    ones are computed, blank lines are ignored."""
    spec = field["control"]["spec"]
    mode, parts = spec["mode"], spec.get("parts", [])
    rdef = row_definition(parts)
    labels = {p["key"]: p.get("label") or p["key"] for p in parts if p.get("type") not in BLOCK_PARTS}
    if mode == "group":
        if not isinstance(value, dict):
            return value, "Un objet est attendu."
        return _row(rdef, labels, value, "")
    if mode == "choice":
        return _choice(field, spec, value)
    if not isinstance(value, list):
        return value, "Une liste est attendue."
    floor = max(spec.get("min", 0), 1 if field.get("required") else 0)
    cap = MAX_ROWS if mode == "repeat" else MAX_ITEMS
    ceiling = spec.get("max", cap)
    if len(value) > cap * 2:
        return value, "Trop d'éléments."
    if mode == "repeat":
        rows: list[dict] = []
        for i, raw in enumerate(value):
            if isinstance(raw, dict) and all(v in (None, "", [], False, {}) for v in raw.values()):
                continue  # a blank line is not a line (and its required sub-fields are not asked)
            kept, problem = _row(rdef, labels, raw, f"Ligne {i + 1} — ")
            if problem:
                return rows, problem
            if kept:  # a blank line is not a line
                rows.append(kept)
        what, result = "ligne", rows
    else:
        items = {o["value"]: o for o in items_of(field)}
        details = has_details(spec)
        picked: dict[str, dict] = {}
        for raw in value:
            v = raw.get("value") if isinstance(raw, dict) else raw
            item = _known(items, field, v)
            if item is None:
                return value, "Choix non proposé."
            if v in picked:
                return value, "Choix en double."
            if item.get("disabled"):
                return value, "Choix indisponible."
            if details:
                extra = {k: x for k, x in raw.items() if k != "value"} if isinstance(raw, dict) else {}
                odef, olabels = _details(spec, item)
                kept, problem = _row(odef, olabels, extra, f"{item.get('label') or v} — ")
                if problem:
                    return value, problem
                picked[v] = {"value": v, **(kept or {})}
            else:
                picked[v] = {"value": v}
        ordered = [picked[v] for v in items if v in picked]  # in the order of the list, whatever order was sent
        ordered += [p for v, p in picked.items() if v not in items]  # options of the service: in the order sent
        what, result = "choix", ordered if details else [p["value"] for p in ordered]
    if len(result) < floor:
        return result, f"Au moins {floor} {what}(s)."
    if len(result) > ceiling:
        return result, f"Au plus {ceiling} {what}(s)."
    return result, ""
