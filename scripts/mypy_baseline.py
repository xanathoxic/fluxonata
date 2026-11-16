#!/usr/bin/env python3
"""Contrôle de typage du backend avec baseline.

Lance mypy (configuration `backend/mypy.ini`) depuis `backend/`, compte les erreurs par fichier et les compare à
`backend/tests/fixtures/mypy_baseline.json`.

Règles :
- un fichier absent de la baseline (propre ou nouveau) doit rester sans erreur : écart BLOQUANT ; un fichier de la baseline ne doit pas en gagner (écart transitoire) ;
- les modules listés dans `bloquants` (préfixes de chemin) doivent avoir 0 erreur : BLOQUANT (code de sortie 1) ;
- les écarts transitoires sont affichés (« transitoire ») et n'échoue qu'avec `--strict`.

Passage au blocage d'une tranche : elle est à 0 erreur, son préfixe est ajouté à `bloquants` (commit dédié).
`--update` réécrit les comptes (jamais à la hausse sans décision : le script refuse si un compte augmente, sauf `--force`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BASELINE = BACKEND / "tests" / "fixtures" / "mypy_baseline.json"
LIGNE = re.compile(r"^(?P<fichier>[^:\s]+\.py):\d+(?::\d+)?: error: ")


class MypyIndisponible(Exception):
    """mypy n'a pas produit un bilan exploitable : jamais interprété comme une absence de régression."""


BILAN_ERREURS = re.compile(r"^Found (\d+) errors? in (\d+) files? \(checked (\d+) source files?\)$", re.M)
BILAN_PROPRE = re.compile(r"^Success: no issues found in (\d+) source files?$", re.M)


def compter(sortie: str) -> Counter[str]:
    return Counter(m["fichier"] for ligne in sortie.splitlines() if (m := LIGNE.match(ligne)))


def analyser(stdout: str, stderr: str, statut: int) -> Counter[str]:
    """Comptes par fichier, après recoupement : statut, bilan annoncé par mypy, fichiers lus.

    Toute incohérence (panne, configuration invalide, erreur sans fichier, bilan absent, aucun fichier lu) lève
    MypyIndisponible.
    """
    if statut not in (0, 1):
        raise MypyIndisponible(f"mypy a échoué (statut {statut}) :\n{stdout}{stderr}")
    comptes = compter(stdout)
    propre, erreurs = BILAN_PROPRE.search(stdout), BILAN_ERREURS.search(stdout)
    if propre:
        lus, annonce = int(propre[1]), 0
    elif erreurs:
        lus, annonce = int(erreurs[3]), int(erreurs[1])
    else:
        raise MypyIndisponible(f"bilan de mypy introuvable :\n{stdout}{stderr}")
    if lus == 0:
        raise MypyIndisponible("mypy n'a lu aucun fichier source (périmètre ou configuration invalide).")
    if (statut == 0) != (annonce == 0) or sum(comptes.values()) != annonce:
        raise MypyIndisponible(
            f"bilan incohérent : {annonce} erreur(s) annoncée(s), {sum(comptes.values())} comptée(s) par fichier, statut {statut}."
        )
    return comptes


def comparer(courant: Counter[str], base: dict) -> tuple[list[str], list[str]]:
    """Renvoie (écarts bloquants, écarts transitoires)."""
    comptes: dict[str, int] = base["fichiers"]
    bloquants = tuple(base.get("bloquants", []))
    dur: list[str] = []
    doux: list[str] = []
    for fichier in sorted(set(courant) | set(comptes)):
        n, ref = courant.get(fichier, 0), comptes.get(fichier, 0)
        en_bloc = bool(bloquants) and fichier.startswith(bloquants)
        if en_bloc and n > 0:
            dur.append(f"{fichier} : {n} erreur(s), tranche bloquante (0 attendu)")
        elif n > 0 and fichier not in comptes:
            dur.append(f"{fichier} : {n} erreur(s), fichier propre ou nouveau (0 attendu)")
        elif n > ref:
            doux.append(f"{fichier} : {n} erreur(s) pour {ref} en baseline")
    return dur, doux


def lancer_mypy() -> Counter[str]:
    """Exécute mypy dans le processus (API officielle) depuis `backend/`, où `mypy.ini` désigne le périmètre."""
    from mypy import api  # import local : mypy est une dépendance de développement

    with contextlib.chdir(BACKEND):
        stdout, stderr, statut = api.run([])
    return analyser(stdout, stderr, statut)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="échoue aussi sur les écarts transitoires")
    ap.add_argument("--update", action="store_true", help="réécrit la baseline (refus si un compte augmente)")
    ap.add_argument("--force", action="store_true", help="avec --update : accepte une hausse (décision explicite)")
    args = ap.parse_args(argv)

    try:
        courant = lancer_mypy()
    except MypyIndisponible as e:
        print(f"ÉCHEC : contrôle de typage inexploitable, aucune conclusion sur les régressions.\n{e}", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"bloquants": [], "fichiers": {}}
    total = sum(courant.values())

    if args.update:
        hausses = [f for f, n in courant.items() if BASELINE.exists() and n > base["fichiers"].get(f, 0)]
        if hausses and not args.force:
            print("Refus : hausse sur " + ", ".join(sorted(hausses)), file=sys.stderr)
            return 1
        base["fichiers"] = dict(sorted(courant.items()))
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Baseline écrite : {total} erreur(s) dans {len(courant)} fichier(s).")
        return 0

    dur, doux = comparer(courant, base)
    ref = sum(base["fichiers"].values())
    print(f"Typage : {total} erreur(s) dans {len(courant)} fichier(s) ; baseline {ref}.")
    for ligne in dur:
        print(f"  BLOQUANT  {ligne}")
    for ligne in doux:
        print(f"  RÉGRESSION (non bloquante)  {ligne}")
    if dur or (args.strict and doux):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
