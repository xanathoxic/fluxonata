#!/usr/bin/env python3
"""Contrôle du linter du frontend (Biome) avec baseline.

Lance `biome lint` (configuration `frontend/biome.json`) depuis `frontend/`, compte les diagnostics par fichier et les compare à
`frontend/lint-baseline.json`.

Règles :
- un fichier absent de la baseline (propre ou nouveau) doit rester sans diagnostic : écart BLOQUANT (code de sortie 1) ;
- les modules listés dans `bloquants` (préfixes de chemin) doivent avoir 0 diagnostic : BLOQUANT ;
- un fichier de la baseline qui gagne des diagnostics : écart « transitoire », affiché, qui n'échoue qu'avec `--strict`.

Un échec de l'outil (panne, configuration invalide, JSON illisible, bilan incohérent, aucun fichier lu) donne le code 2 et
n'est JAMAIS interprété comme une absence de régression.
Passage au blocage d'un périmètre : il est à 0 diagnostic, son préfixe est ajouté à `bloquants` (commit dédié).
`--update` réécrit les comptes (refus si un compte augmente, sauf `--force`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BASELINE = FRONTEND / "lint-baseline.json"


class LinterIndisponible(Exception):
    """Biome n'a pas produit un bilan exploitable."""


def analyser(stdout: str, statut: int) -> tuple[Counter[str], Counter[str]]:
    """(diagnostics par fichier, diagnostics par règle), après recoupement du bilan annoncé par l'outil."""
    if statut not in (0, 1):
        raise LinterIndisponible(f"biome a échoué (statut {statut}).")
    try:
        doc = json.loads(stdout)
        resume, diagnostics = doc["summary"], doc["diagnostics"]
        annonces = resume["errors"] + resume["warnings"] + resume["infos"]
        fichiers_lus = resume["unchanged"] + resume["changed"]
        non_affiches = resume["diagnosticsNotPrinted"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LinterIndisponible(f"sortie de biome illisible ({type(exc).__name__}).") from None
    if fichiers_lus == 0:
        raise LinterIndisponible("biome n'a lu aucun fichier (périmètre ou configuration invalide).")
    if non_affiches or annonces != len(diagnostics):
        raise LinterIndisponible(
            f"bilan incohérent : {annonces} annoncé(s), {len(diagnostics)} reçu(s), {non_affiches} non affiché(s)."
        )
    if resume["errors"] and statut != 1:
        raise LinterIndisponible(f"{resume['errors']} erreur(s) annoncée(s) mais statut {statut}.")
    par_fichier = Counter(d["location"]["path"] for d in diagnostics)
    par_regle = Counter(d["category"] for d in diagnostics)
    return par_fichier, par_regle


def comparer(courant: Counter[str], base: dict) -> tuple[list[str], list[str]]:
    """Renvoie (écarts bloquants, écarts transitoires)."""
    comptes: dict[str, int] = base["fichiers"]
    bloquants = tuple(base.get("bloquants", []))
    dur: list[str] = []
    doux: list[str] = []
    for fichier in sorted(set(courant) | set(comptes)):
        n, ref = courant.get(fichier, 0), comptes.get(fichier, 0)
        if bloquants and fichier.startswith(bloquants) and n > 0:
            dur.append(f"{fichier} : {n} diagnostic(s), périmètre bloquant (0 attendu)")
        elif n > 0 and fichier not in comptes:
            dur.append(f"{fichier} : {n} diagnostic(s), fichier propre ou nouveau (0 attendu)")
        elif n > ref:
            doux.append(f"{fichier} : {n} diagnostic(s) pour {ref} en baseline")
    return dur, doux


def lancer_biome() -> tuple[Counter[str], Counter[str]]:
    p = subprocess.run(
        ["npx", "--no-install", "biome", "lint", "--reporter=json", "--max-diagnostics=10000", "."],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        check=False,
    )
    return analyser(p.stdout, p.returncode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="échoue aussi sur les écarts transitoires")
    ap.add_argument("--update", action="store_true", help="réécrit la baseline (refus si un compte augmente)")
    ap.add_argument("--force", action="store_true", help="avec --update : accepte une hausse (décision explicite)")
    args = ap.parse_args(argv)
    try:
        courant, par_regle = lancer_biome()
    except (LinterIndisponible, OSError) as e:
        print(f"ÉCHEC : contrôle du linter inexploitable, aucune conclusion sur les régressions.\n{e}", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"bloquants": [], "fichiers": {}}
    total = sum(courant.values())
    if args.update:
        hausses = [f for f, n in courant.items() if BASELINE.exists() and n > base["fichiers"].get(f, 0)]
        if hausses and not args.force:
            print("Refus : hausse sur " + ", ".join(sorted(hausses)), file=sys.stderr)
            return 1
        base["fichiers"] = dict(sorted(courant.items()))
        base["par_regle"] = dict(par_regle.most_common())
        BASELINE.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Baseline écrite : {total} diagnostic(s) dans {len(courant)} fichier(s).")
        return 0
    dur, doux = comparer(courant, base)
    print(
        f"Linter frontend : {total} diagnostic(s) dans {len(courant)} fichier(s) ; baseline {sum(base['fichiers'].values())}."
    )
    for ligne in dur:
        print(f"  BLOQUANT  {ligne}")
    for ligne in doux:
        print(f"  RÉGRESSION (non bloquante)  {ligne}")
    return 1 if dur or (args.strict and doux) else 0


if __name__ == "__main__":
    sys.exit(main())
