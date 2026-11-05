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
