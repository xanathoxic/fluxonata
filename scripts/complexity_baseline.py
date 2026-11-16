#!/usr/bin/env python3
"""Contrôle de la complexité cyclomatique (Ruff C901, seuil 10) avec baseline décroissante.

La baseline `backend/tests/fixtures/complexity_baseline.json` liste les fonctions qui dépassent encore le seuil, avec leur complexité
actuelle. Règles :
- une fonction absente de la baseline qui dépasse le seuil : ÉCHEC (aucune nouvelle fonction trop complexe) ;
- une fonction de la baseline dont la complexité AUGMENTE : ÉCHEC ;
- une fonction qui baisse ou disparaît : succès, avec rappel de réécrire la baseline (`--update`, qui refuse toute hausse) ;
- un échec de l'outil (Ruff absent, sortie illisible) : code 2, jamais interprété comme une absence de régression.
La clé est `fichier::fonction` (sans numéro de ligne : un déplacement dans le fichier ne change rien).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "backend" / "tests" / "fixtures" / "complexity_baseline.json"
MESSAGE = re.compile(r"`(?P<name>.+)` is too complex \((?P<score>\d+) > (?P<limit>\d+)\)")


def measure(ruff: str) -> dict[str, int]:
    """Complexité des fonctions au-dessus du seuil, pour tout le backend (migrations exclues par la configuration).
    `--ignore-noqa` : un `# noqa: C901` ne cache pas une fonction (12 en cachaient jusqu'au 2026-11-16, dont une à 83)."""
    proc = subprocess.run(
        [ruff, "check", "--select", "C901", "--config", "ruff.toml", "--output-format", "json", "--no-cache", "--ignore-noqa", "."],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        sys.exit(f"Complexité : Ruff a échoué (code {proc.returncode}) : {proc.stderr.strip()[:300]}")
    try:
        found = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        sys.exit("Complexité : sortie de Ruff illisible.")
    scores: dict[str, int] = {}
    for item in found:
        m = MESSAGE.search(item["message"])
        if not m:
            sys.exit(f"Complexité : message inattendu : {item['message'][:200]}")
        path = Path(item["filename"]).resolve().relative_to(ROOT / "backend").as_posix()
        key = f"{path}::{m['name']}"
        scores[key] = max(scores.get(key, 0), int(m["score"]))
    return scores


def compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """(échecs, améliorations) : voir la docstring du module."""
    failures = [f"nouvelle fonction trop complexe : {k} ({v})" for k, v in sorted(current.items()) if k not in baseline]
    failures += [
        f"complexité en hausse : {k} ({baseline[k]} -> {v})"
        for k, v in sorted(current.items())
        if k in baseline and v > baseline[k]
    ]
    better = [k for k, v in sorted(baseline.items()) if current.get(k, 0) < v]
    return failures, better


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update", action="store_true", help="réécrit la baseline (refuse toute hausse ou nouvelle fonction)"
    )
    parser.add_argument("--ruff", default=str(ROOT / ".venv" / "bin" / "ruff"))
    args = parser.parse_args()
    current = measure(args.ruff)
    baseline: dict[str, int] = json.loads(BASELINE.read_text())["functions"] if BASELINE.exists() else {}
    failures, better = compare(current, baseline)
    total, worst = len(current), max(current.values(), default=0)
    print(f"Complexité : {total} fonction(s) au-dessus de 10 (maximum {worst}) ; baseline {len(baseline)}.")
    for line in failures[:25]:
        print(f"  ÉCHEC  {line}")
    if len(failures) > 25:
        print(f"  … et {len(failures) - 25} autre(s)")
    if failures and not (
        args.update and not BASELINE.exists()
    ):  # first run: the baseline is created from the current state
        return 1
    if args.update:
        BASELINE.write_text(
            json.dumps(
                {"_doc": __doc__.splitlines()[0], "functions": dict(sorted(current.items()))},
                indent=1,
                ensure_ascii=False,
            )
            + "\n"
        )
        print("Baseline réécrite.")
    elif better:
        print(f"  {len(better)} fonction(s) améliorée(s) : relancez avec --update pour verrouiller le gain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
