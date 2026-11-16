#!/usr/bin/env python3
"""Contrôle de mise en forme du frontend (Biome, configuration `frontend/biome.format.json`) avec baseline décroissante.

Activer le formateur d'un coup réécrirait presque tous les fichiers (la mise en forme historique est dense et sans règle commune) : un
changement massif qui masquerait les changements métier. À la place, la baseline `frontend/format-baseline.json` liste les fichiers
non conformes ; règles :
- un fichier absent de la baseline (nouveau, ou déjà conforme) qui n'est pas conforme : ÉCHEC ;
- un fichier de la baseline devenu conforme : succès, avec rappel de réécrire la baseline (`--update`, qui refuse toute entrée nouvelle) ;
- un échec de l'outil, un bilan incohérent ou aucun fichier lu : code 2, jamais interprété comme « tout est conforme ».
Pour mettre un fichier en conformité : `cd frontend && npx biome format --write --config-path=biome.format.json <fichier>`, dans un commit
dédié (sans changement de comportement), puis `--update`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BASELINE = FRONTEND / "format-baseline.json"

_spec = importlib.util.spec_from_file_location("lint_frontend_baseline", ROOT / "scripts" / "lint_frontend_baseline.py")
_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lint)  # same reading and cross-checking of Biome's report as the lint control


def unformatted() -> set[str]:
    proc = subprocess.run(
        ["npx", "biome", "format", "--config-path=biome.format.json", "--reporter=json", "."],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        per_file, _ = _lint.analyser(proc.stdout, proc.returncode)
    except _lint.LinterIndisponible as exc:
        print(f"Mise en forme : {exc}", file=sys.stderr)
        sys.exit(2)
    return set(per_file)


def compare(current: set[str], baseline: set[str]) -> tuple[list[str], list[str]]:
    """(échecs, fichiers devenus conformes)."""
    return sorted(current - baseline), sorted(baseline - current)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="réécrit la baseline (refuse tout fichier nouveau)")
    args = parser.parse_args()
    current = unformatted()
    baseline = set(json.loads(BASELINE.read_text())["fichiers"]) if BASELINE.exists() else set()
    failures, fixed = compare(current, baseline)
    print(f"Mise en forme : {len(current)} fichier(s) non conforme(s) ; baseline {len(baseline)}.")
    for path in failures[:25]:
        print(f"  ÉCHEC  {path} : non conforme à biome.format.json (fichier absent de la baseline)")
    if failures and not (args.update and not BASELINE.exists()):
        return 1
    if args.update:
        BASELINE.write_text(
            json.dumps({"_doc": __doc__.splitlines()[0], "fichiers": sorted(current)}, indent=1, ensure_ascii=False)
            + "\n"
        )
        print("Baseline réécrite.")
    elif fixed:
        print(f"  {len(fixed)} fichier(s) devenu(s) conforme(s) : relancez avec --update pour verrouiller le gain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
