#!/usr/bin/env python3
"""Contrôle des migrations pour les mises à jour progressives (AR5.6, docs/ROADMAP_ARCHITECTURE.md).

Pendant un déploiement par rôles l'ANCIEN code tourne encore sur le NOUVEAU schéma (`scripts/compat-test.sh`) : une migration doit donc être « expand » (ajouter) puis,
une version plus tard seulement, « contract » (retirer). Ce script lit chaque migration NOUVELLE (absente de la base gelée `backend/tests/fixtures/migrations_baseline.json`)
et refuse ce qui casse l'ancien code ou bloque les écritures, sauf dérogation écrite dans le fichier :

    # lint-migrations: allow M1 — colonne plus lue depuis la version 0.4, retrait prévu en 0.5

Règles : M1 retrait/renommage d'un champ ou d'un modèle (casse l'ancien code) · M2 champ non nul SANS valeur par défaut (l'ancien code ne le renseigne pas : INSERT en échec) ·
M3 index ou contrainte sur table existante, hors CONCURRENTLY (verrou d'écriture le temps de la construction) · M4 SQL brut qui supprime ou renomme (DROP/RENAME).

    python scripts/lint_migrations.py            # contrôle ; code 1 s'il y a une violation non dérogée
    python scripts/lint_migrations.py --write    # gèle toutes les migrations actuelles comme « déjà examinées » (à faire une fois, ou après revue)
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
BASELINE = BACKEND / "tests" / "fixtures" / "migrations_baseline.json"
WAIVER = re.compile(r"#\s*lint-migrations:\s*allow\s+([M0-9, ]+)")
CONTRACT_OPS = {"RemoveField", "DeleteModel", "RenameField", "RenameModel"}
INDEX_OPS = {
    "AddIndex",
    "AddConstraint",
}  # AddIndexConcurrently est la forme sûre : non listé
RISKY_SQL = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|INDEX|CONSTRAINT)|RENAME\s+(TO|COLUMN)|ALTER\s+COLUMN\s+\S+\s+SET\s+NOT\s+NULL)\b",
    re.IGNORECASE,
)


def migration_files() -> list[Path]:
    return sorted(p for p in BACKEND.glob("*/migrations/[0-9]*.py"))


def key(path: Path) -> str:
    return f"{path.parent.parent.name}/{path.stem}"


def _name(node) -> str:
    f = node.func if isinstance(node, ast.Call) else node
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _kw(call: ast.Call) -> dict:
    return {k.arg: k.value for k in call.keywords if k.arg}


def check(path: Path) -> list[tuple[str, str]]:
    """[(règle, explication)] des violations de CE fichier, dérogations déduites."""
    text = path.read_text(encoding="utf-8")
    allowed = {
        r.strip()
        for m in WAIVER.finditer(text)
        for r in m.group(1).split(",")
        if r.strip()
    }
    found: list[tuple[str, str]] = []
    tree = ast.parse(text)
    created = {  # tables créées PAR cette migration : un index ou une contrainte dessus ne bloque personne (elle est vide et inconnue de l'ancien code)
        _kw(n)["name"].value.lower()
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and _name(n) == "CreateModel"
        and isinstance(_kw(n).get("name"), ast.Constant)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        op = _name(node)
        if op in CONTRACT_OPS:
            found.append(
                (
                    "M1",
                    f"{op} : l'ancien code lit encore ce champ ou ce modèle pendant le déploiement (retirer une version plus tard)",
                )
            )
        elif op == "AddField":
            field = next(
                (
                    a
                    for a in list(node.args)
                    + [k.value for k in node.keywords if k.arg == "field"]
                    if isinstance(a, ast.Call)
                ),
                None,
            )
            if field is not None:
                kw = _kw(field)
                nullable = (
                    isinstance(kw.get("null"), ast.Constant)
                    and kw["null"].value is True
                )
                if (
                    not nullable
                    and "default" not in kw
                    and _name(field) not in ("ManyToManyField",)
                ):
                    found.append(
                        (
                            "M2",
                            f"AddField {_name(field)} non nul sans `default` : l'ancien code ne le renseigne pas, ses INSERT échouent",
                        )
                    )
        elif op in INDEX_OPS:
            target = _kw(node).get("model_name")
            if (
                isinstance(target, ast.Constant)
                and str(target.value).lower() in created
            ):
                continue
            found.append(
                (
                    "M3",
                    f"{op} : construction avec verrou d'écriture sur la table (utiliser AddIndexConcurrently, ou déroger si la table est petite)",
                )
            )
        elif op == "RunSQL":
            sql = " ".join(
                a.value
                for a in ast.walk(node)
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            )
            if RISKY_SQL.search(sql):
                found.append(
                    (
                        "M4",
                        "RunSQL qui supprime/renomme/rend non nul : casse l'ancien code",
                    )
                )
    return [(r, why) for r, why in found if r not in allowed]


def baseline() -> set[str]:
    return (
        set(json.loads(BASELINE.read_text(encoding="utf-8"))["reviewed"])
        if BASELINE.exists()
        else set()
    )


def main(argv: list[str]) -> int:
    files = migration_files()
    if "--write" in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {
                    "_doc": "Migrations déjà examinées (scripts/lint_migrations.py --write). Toute migration ABSENTE d'ici est contrôlée.",
                    "reviewed": sorted(key(p) for p in files),
                },
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"base écrite : {len(files)} migrations examinées")
        return 0
    seen, bad = baseline(), 0
    new = [p for p in files if key(p) not in seen]
    for p in new:
        for rule, why in check(p):
            bad += 1
            print(
                f"{key(p)} : {rule} — {why}\n   dérogation : `# lint-migrations: allow {rule} — raison` dans le fichier, après revue"
            )
    print(
        f"{len(files)} migrations, {len(new)} nouvelle(s) contrôlée(s), {bad} violation(s)"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
