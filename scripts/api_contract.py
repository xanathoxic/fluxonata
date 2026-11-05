#!/usr/bin/env python3
"""Contrat de l'API publique (AR6.2, docs/VERSIONING.md) : une opération (méthode + chemin) de la v1 ne disparaît pas sans décision.

    .venv/bin/python scripts/api_contract.py            # compare le schéma généré à la base gelée ; code 1 si une opération a DISPARU
    .venv/bin/python scripts/api_contract.py --write    # regèle la base (décision consciente : suppression annoncée, ou ajouts à enregistrer)

Seules les SUPPRESSIONS échouent (les ajouts sont compatibles). Ne couvre pas encore les champs : une propriété retirée d'une réponse, ou rendue obligatoire dans une
requête, n'est pas détectée (limite connue, voir docs/VERSIONING.md).
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "backend" / "tests" / "fixtures" / "api_operations_baseline.json"


def operations() -> list[str]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fluxonata.settings")
    os.environ.setdefault("ORCH_DEBUG", "1")
    sys.path.insert(0, str(ROOT / "backend"))
    import django

    django.setup()
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    return sorted(
        f"{m.upper()} {p}"
        for p, item in schema["paths"].items()
        for m in item
        if m in {"get", "post", "put", "patch", "delete"}
    )


def main(argv) -> int:
    ops = operations()
    if "--write" in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {
                    "_doc": "Opérations de l'API v1 (scripts/api_contract.py --write).",
                    "operations": ops,
                },
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"base écrite : {len(ops)} opérations")
        return 0
    frozen = set(json.loads(BASELINE.read_text(encoding="utf-8"))["operations"])
    gone, added = sorted(frozen - set(ops)), sorted(set(ops) - frozen)
    for o in gone:
        print(f"OPÉRATION DISPARUE : {o}")
    if added:
        print(
            f"{len(added)} opération(s) nouvelle(s) non encore gelée(s) (compatible) : relancer avec --write pour les enregistrer"
        )
    print(f"{len(ops)} opérations, {len(gone)} disparue(s)")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
