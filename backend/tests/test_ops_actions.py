"""The utility actions: contract complete, `run()` honest in real and test mode, output matches its schema."""

import jsonschema
import pytest

from catalog import contract
from catalog.ops_actions import OPS_ACTIONS

CASES = {
    "parse-hosts": (
        {"text": "Web-01, web-01;db-01 bad_host!"},
        lambda o: o["hosts"] == ["web-01", "db-01"] and o["rejected"] == ["bad_host!"],
    ),
    "check-vm-request": (
        {"cpu": 64, "ram_gb": 8, "disk_gb": 10, "environment": "dev"},
        lambda o: not o["acceptable"] and len(o["problems"]) == 1,
    ),
    "check-maintenance-window": ({"now": "2026-01-03T23:30:00"}, lambda o: o["in_window"] is True),
    "aggregate-results": (
        {"results": [{"name": "a", "status": "ok"}, {"name": "b", "status": "echec"}]},
        lambda o: o["success_rate"] == 50 and o["failed_names"] == ["b"],
    ),
    "render-message": (
        {"template": "Salut {{a}} {{b}}", "values": {"a": "x"}},
        lambda o: o["message"] == "Salut x {{b}}" and o["missing"] == ["b"],
    ),
    "scan-log": ({"text": "INFO a\nERROR b"}, lambda o: o["errors"] == 1 and o["worst"] == "ERROR"),
    "redact-text": (
        {"text": "a@b.fr 10.0.0.1 token=zz"},
        lambda o: o["replacements"] == 3 and "@" not in o["redacted"],
    ),
    "compute-deadline": (
        {"start": "2026-01-05T09:00:00", "hours": 2, "now": "2026-01-05T12:00:00"},
        lambda o: o["overdue"] and o["hours_left"] == -1.0,
    ),
    "compare-lists": (
        {"expected": ["a", "b"], "actual": ["b", "c"]},
        lambda o: o["missing"] == ["a"] and o["extra"] == ["c"],
    ),
}


def _run(spec, inputs):
    ns: dict = {}
    exec(spec["source"], ns)  # noqa: S102  (the action's own code, run here only to test it)
    jsonschema.validate(inputs, spec["input_schema"])
    out = ns["run"](inputs)
    jsonschema.validate(out, spec["output_schema"])
    return out


@pytest.mark.parametrize("spec", OPS_ACTIONS, ids=lambda s: s["slug"])
def test_contract_is_complete(spec):
    assert not contract.problems(spec["description"], spec["input_schema"], spec["output_schema"])
    assert spec["input_schema"]["properties"]["mode"]["enum"] == ["reel", "test"]
    assert "simulated" in spec["output_schema"]["properties"]


@pytest.mark.parametrize("spec", OPS_ACTIONS, ids=lambda s: s["slug"])
def test_real_mode(spec):
    inputs, check = CASES[spec["slug"]]
    out = _run(spec, {**inputs, "mode": "reel"})
    assert out["simulated"] is False
    assert check(out)


@pytest.mark.parametrize("spec", OPS_ACTIONS, ids=lambda s: s["slug"])
def test_test_mode_runs_without_inputs(spec):
    out = _run(spec, {"mode": "test"})
    assert out["simulated"] is True


@pytest.mark.parametrize("spec", OPS_ACTIONS, ids=lambda s: s["slug"])
def test_real_mode_refuses_missing_input(spec):
    with pytest.raises(ValueError):
        _run(spec, {"mode": "reel"})
