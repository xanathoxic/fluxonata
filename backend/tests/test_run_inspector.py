"""Variable inspector of a run: resolved inputs per step, workflow inputs/constants/outputs, secrets masked."""

import pytest

from runs import inspector
from runs.catalog_views import MASK
from tests.conftest import client_for
from tests.wf_helpers import drain, launch, make_action, node, publish, schema

STR = {"type": "string"}


@pytest.fixture
def run(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(
        ws,
        "insp",
        [node("a", action="echo", version="1.0.0", inputs={"value": "{{ inputs.who }}"})],
        [],
        inputs=schema(["who", "password"], who=STR, password={"type": "string", "format": "password"}),
        outputs={"result": "{{ steps.a.output.value }}"},
    )
    return drain(launch(ws, v, world["designer"], inputs={"who": "alice", "password": "hunter2"}))


def test_inspector_values_and_masking(run):
    d = inspector.build(run, {s.node_id: s for s in run.steps.all()})
    assert d["start"]["inputs"] == {"who": "alice", "password": MASK}
    assert d["start"]["constants"] == {}
    assert d["nodes"]["a"]["inputs"] == {"value": "alice"}
    assert d["end"]["outputs"] == {"result": "alice"}
    assert "hunter2" not in str(d)


def test_inspector_exposed_by_run_api(run, world):
    r = client_for(world["designer"]).get(f"/api/v1/workspaces/{run.workspace.slug}/workflow-runs/{run.id}/")
    assert r.status_code == 200
    assert r.json()["inspector"]["start"]["inputs"]["password"] == MASK
    assert "hunter2" not in str(r.json()["inspector"])
