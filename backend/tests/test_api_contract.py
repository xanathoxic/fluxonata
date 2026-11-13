"""AR6.2 — une opération de l'API v1 ne disparaît pas sans décision (scripts/api_contract.py)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "api_contract.py"
spec = importlib.util.spec_from_file_location("api_contract", SCRIPT)
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)


def test_no_operation_of_the_frozen_v1_contract_has_disappeared():
    frozen = set(json.loads(ac.BASELINE.read_text(encoding="utf-8"))["operations"])
    now = set(ac.operations())
    assert frozen - now == set(), (
        f"opération(s) retirée(s) de l'API v1 : {sorted(frozen - now)} — une suppression se décide, s'annonce (docs/VERSIONING.md) puis se regèle"
    )


def test_the_contract_is_substantial_and_versioned():
    ops = json.loads(ac.BASELINE.read_text(encoding="utf-8"))["operations"]
    assert len(ops) > 200 and all(o.split(" ", 1)[1].startswith("/api/v1/") for o in ops)


def test_a_removed_operation_is_reported_and_an_added_one_is_not_a_failure(tmp_path, monkeypatch, capsys):
    base = tmp_path / "b.json"
    base.write_text(json.dumps({"operations": ["GET /api/v1/a", "POST /api/v1/b"]}))
    monkeypatch.setattr(ac, "BASELINE", base)
    monkeypatch.setattr(ac, "operations", lambda: ["GET /api/v1/a", "GET /api/v1/c"])
    assert ac.main([]) == 1 and "OPÉRATION DISPARUE : POST /api/v1/b" in capsys.readouterr().out
    monkeypatch.setattr(ac, "operations", lambda: ["GET /api/v1/a", "POST /api/v1/b", "GET /api/v1/c"])
    assert ac.main([]) == 0


def test_docs_openapi_json_is_fresh():
    """`docs/openapi.json` est le schéma réel : toute route ajoutée ou modifiée exige de le régénérer."""
    from drf_spectacular.generators import SchemaGenerator

    committed = json.loads((SCRIPT.parents[1] / "docs" / "openapi.json").read_text(encoding="utf-8"))
    generated = json.loads(json.dumps(SchemaGenerator().get_schema(request=None, public=True)))
    assert committed == generated, (
        "docs/openapi.json n'est plus à jour : cd backend && ../.venv/bin/python manage.py spectacular "
        "--format openapi-json --file ../docs/openapi.json (puis scripts/api_contract.py --write si des opérations sont ajoutées)"
    )


KNOWN_NAMING_WARNINGS = (
    "enum naming encountered",
    "encountered multiple names for the same choice set",
    "has collisions",
)


def test_schema_generation_has_no_warning_nor_error_except_the_open_naming_ones():
    """Aucune cause d'avertissement technique ne doit revenir : authentificateur sans extension de schéma, deux serializers de même
    nom de composant (le schéma décrirait l'un pour l'autre), indice de type absent. Les 22 avertissements de NOMMAGE public
    (énumérations, identifiants d'opération) restent ouverts : ils relèvent d'une décision sur les noms que voient les clients générés."""
    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.generators import SchemaGenerator

    GENERATOR_STATS.reset()
    SchemaGenerator().get_schema(request=None, public=True)
    unexpected = [w for w in GENERATOR_STATS._warn_cache if not any(k in w for k in KNOWN_NAMING_WARNINGS)]
    assert not unexpected and not GENERATOR_STATS._error_cache, (
        f"avertissements : {unexpected[:5]} ; erreurs : {list(GENERATOR_STATS._error_cache)[:5]}"
    )
    assert len(GENERATOR_STATS._warn_cache) <= 22, "de nouveaux conflits de nommage sont apparus"
