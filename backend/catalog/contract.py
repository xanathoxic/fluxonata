"""The CONTRACT of an action: a plain, complete statement of what it does, what it takes and what it returns.

An action is a building block other people (and the assistant) wire into workflows without reading its code, so its
contract is what they rely on:
- what the action does (its description: at least a sentence);
- for EVERY input: its name, type, whether it is required, and what it is for; the same for EVERY output;
- when there is no input, or no output, that is said explicitly (the description of the schema), never left to be guessed.

The JSON Schemas of the version carry it (`title`, `description`, `type`, `default`, limits); this module checks that it is
complete when a version is published, and presents it in a form a person reads. Published versions are immutable: an
older version that lacks parts of the contract is reported as incomplete, and completed by publishing a new version."""

MIN_SUMMARY = 30  # characters: a sentence, not a label
MIN_PARAM = 12
KIND = {
    "string": "texte",
    "integer": "entier",
    "number": "nombre",
    "boolean": "booléen",
    "array": "liste",
    "object": "objet",
    "null": "vide",
}


def _type_text(schema: dict) -> str:
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    words = [KIND.get(k, str(k)) for k in kinds if k != "null"]
    text = " ou ".join(words) or "non précisé"
    items = schema.get("items")
    if "array" in kinds and isinstance(items, dict) and items.get("type"):
        text = f"liste de {_type_text(items)}"
    if "null" in kinds:
        text += " (peut être vide)"
    return text


def _limits(schema: dict) -> str:
    parts = []
    if schema.get("enum"):
        parts.append("valeurs possibles : " + ", ".join(str(v) for v in schema["enum"]))
    for key, label in (
        ("minimum", "minimum"),
        ("maximum", "maximum"),
        ("minLength", "longueur minimale"),
        ("maxLength", "longueur maximale"),
        ("minItems", "au moins"),
        ("maxItems", "au plus"),
    ):
        if key in schema:
            parts.append(f"{label} {schema[key]}")
    if schema.get("pattern"):
        parts.append(f"format {schema['pattern']}")
    if schema.get("format"):
        parts.append(f"format {schema['format']}")
    return " ; ".join(parts)


def fields_of(schema) -> list[dict]:
    """The parameters of an object schema, in the form shown to a person."""
    schema = schema if isinstance(schema, dict) else {}
    required = set(schema.get("required") or [])
    rows = []
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        rows.append(
            {
                "name": name,
                "title": spec.get("title", ""),
                "type": _type_text(spec),
                "required": name in required,
                "description": str(spec.get("description", "")).strip(),
                "default": spec.get("default"),
                "has_default": "default" in spec,
                "limits": _limits(spec),
            }
        )
    return rows


def problems(description: str, input_schema, output_schema) -> list[str]:
    """What is missing from the contract (French sentences, each one says what to add)."""
    out: list[str] = []
    if len((description or "").strip()) < MIN_SUMMARY:
        out.append(
            "Description de l'action : dites en une ou deux phrases ce que fait l'action (au moins 30 caractères)."
        )
    for label, schema in (("Entrée", input_schema), ("Sortie", output_schema)):
        schema = schema if isinstance(schema, dict) else {}
        props = schema.get("properties") or {}
        if not props:
            if len(str(schema.get("description", "")).strip()) < 5:
                none = "aucune entrée" if label == "Entrée" else "aucune sortie"
                out.append(
                    f"{label}s : cette action n'a pas de paramètre ; dites-le dans la « description » du schéma "
                    f"(ex. « Cette action n'a {none}. »)."
                )
            continue
        for name, spec in props.items():
            spec = spec if isinstance(spec, dict) else {}
            if not spec.get("type"):
                out.append(f"{label} « {name} » : indiquez son type (string, integer, number, boolean, array, object).")
            if not str(spec.get("title", "")).strip():
                out.append(f"{label} « {name} » : ajoutez un titre court (champ « title »).")
            if len(str(spec.get("description", "")).strip()) < MIN_PARAM:
                purpose = "à quoi sert ce paramètre" if label == "Entrée" else "ce que contient cette valeur"
                out.append(f"{label} « {name} » : ajoutez une description ({purpose}).")
    return out


def contract(description: str, input_schema, output_schema, version) -> dict:
    """The contract as the API returns it."""
    miss = problems(description, input_schema, output_schema)
    inputs, outputs = fields_of(input_schema), fields_of(output_schema)
    return {
        "summary": (description or "").strip(),
        "inputs": inputs,
        "inputs_note": "" if inputs else str((input_schema or {}).get("description", "")).strip() or "Aucune entrée.",
        "outputs": outputs,
        "outputs_note": ""
        if outputs
        else str((output_schema or {}).get("description", "")).strip() or "Aucune sortie.",
        "effects": {
            "side_effects": getattr(version, "side_effects", "none"),
            "idempotent": getattr(version, "idempotent", True),
            "network_policy": getattr(version, "network_policy", "none"),
            "needs_connections": [
                s.get("slot") for s in (getattr(version, "secrets_required", None) or []) if isinstance(s, dict)
            ],
        },
        "complete": not miss,
        "problems": miss,
    }
