"""Tags (« étiquettes ») on workflows, actions and AI skills: several per object, free words that group and find things.

A tag is lowercase, one word (spaces become « - »), letters (accents allowed), digits and « - _ . », 30 characters at most;
an object carries at most ten, without duplicates. Cleaning is lenient on input (case, spaces) and strict on the result."""

import re

from rest_framework import serializers

MAX_TAGS = 10
MAX_LEN = 30
_OK = re.compile(r"^[^\W_][\w.-]*$", re.UNICODE)


def clean_tags(raw, field: str = "tags") -> list[str]:
    """Normalise a list of tags (a comma-separated string is accepted). Raises a field error when it is invalid."""
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        raise serializers.ValidationError({field: "Une liste d'étiquettes est attendue."})
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise serializers.ValidationError({field: "Chaque étiquette est un texte."})
        tag = re.sub(r"\s+", "-", item.strip().lower())
        if not tag:
            continue
        if len(tag) > MAX_LEN or not _OK.match(tag):
            raise serializers.ValidationError(
                {
                    field: f"« {item.strip()} » n'est pas une étiquette valide (lettres, chiffres, - _ . ; {MAX_LEN} caractères au plus)."
                }
            )
        if tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise serializers.ValidationError({field: f"{MAX_TAGS} étiquettes au plus par élément."})
    return out
