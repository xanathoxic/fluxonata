#!/usr/bin/env python3
"""Compte les marqueurs d'exemption de ruff (« # » suivi de « no » et « qa ») d'une révision Git, par composant du lot 0 de la conformité globale, de façon reproductible.

Méthode : `git grep` sur la révision (fichiers suivis seulement, pas de copie de travail), motif « # » + marqueur (casse ignorée, comme ruff) ;
on compte les LIGNES contenant le motif (une ligne avec deux codes compte une fois) ; « avec code » = marqueur suivi de « : » et d'un
code de règle, « nu » = sans code (désactive toutes les règles de la ligne). Les chemins de chaque composant sont listés
ci-dessous : deux comptages ne se comparent que pour la même révision logique, les mêmes chemins et la même méthode.

    python3 scripts/count_noqa.py [--rev HEAD] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MARK = "no" + "qa"  # écrit en deux morceaux : le script ne se compte pas lui-même
NOQA = rf"#\s*{MARK}"
CODED = re.compile(rf"#\s*{MARK}:\s*[A-Z]+[0-9]+", re.I)
# composant -> (chemins inclus, chemins exclus), en pathspecs Git
COMPONENTS: dict[str, tuple[list[str], list[str]]] = {
    "C1 backend applicatif": (["backend/*.py"], [":!backend/tests", ":!*/migrations/*"]),
    "C2 migrations": (["backend/*/migrations/*.py"], []),
    "C3 tests du backend": (["backend/tests/*.py"], []),
    "C6 harnais et agent": (["workers", "runner"], []),
    "C7 scripts et bancs": (["scripts", "tests"], []),
    "C11 exemples": (["examples"], []),
}


def count(rev: str, include: list[str], exclude: list[str]) -> dict[str, int]:
    # `git grep` sort 1 sans résultat : ce n'est pas une erreur ; au-delà, c'est une vraie panne.
    p = subprocess.run(
        ["git", "grep", "-I", "-i", "-h", "-E", NOQA, rev, "--", *include, *exclude], capture_output=True, text=True
    )
    if p.returncode not in (0, 1):
        raise RuntimeError(p.stderr.strip())
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    coded = sum(1 for ln in lines if CODED.search(ln))
    return {"lignes": len(lines), "avec_code": coded, "nus": len(lines) - coded}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", args.rev], capture_output=True, text=True, check=True
    ).stdout.strip()
    out = {name: count(args.rev, inc, exc) for name, (inc, exc) in COMPONENTS.items()}
    if args.json:
        print(json.dumps({"revision": commit, "composants": out}, ensure_ascii=False, indent=1))
    else:
        print(f"Exemptions ruff — révision {commit}")
        for name, c in out.items():
            print(f"  {name:<24} {c['lignes']:>4} lignes ({c['avec_code']} avec code, {c['nus']} nus)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
