"""backend/constraints.txt : versions exactes des dépendances d'exécution, cohérentes avec requirements.txt."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def pins(path):
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            out[re.sub(r"[-_.]+", "-", name).lower()] = version
    return out


def test_constraints_are_exact_unique_pins():
    lines = [
        x.strip() for x in (BACKEND / "constraints.txt").read_text().splitlines() if x.strip() and not x.startswith("#")
    ]
    assert lines, "aucune contrainte"
    assert all(re.fullmatch(r"[A-Za-z0-9._-]+==[0-9][A-Za-z0-9.+!_-]*", x) for x in lines)
    assert len({x.partition("==")[0].lower() for x in lines}) == len(lines), "paquet en double"


def test_every_direct_requirement_is_pinned_to_the_same_version():
    direct, locked = pins(BACKEND / "requirements.txt"), pins(BACKEND / "constraints.txt")
    assert direct
    assert {k: v for k, v in direct.items() if locked.get(k) != v} == {}, (
        "version directe absente ou différente des contraintes"
    )
    assert len(locked) > len(direct), "les dépendances transitives doivent figurer aussi"
