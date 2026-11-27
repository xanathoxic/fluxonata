"""Un champ facultatif à null est un champ omis (`default(inputs.x, null)` en produit) ; un champ obligatoire à null reste une erreur."""

from runs.models import WorkflowRun
from runs.serializers import drop_null_optionals, validate_inputs_against, with_defaults
from tests.wf_helpers import STR, drain, launch, node, publish, schema
from triggers import formengine
from workflows.validation import validate_graph

INT = {"type": "integer"}
BOOL = {"type": "boolean"}
SCHEMA = schema(["vlan_id"], vlan_id=INT, vlan_vni=INT, vlan_name=STR, vlan_enabled=BOOL)
OPT = ("vlan_vni", "vlan_name", "vlan_enabled")


def child_outputs():
    return {
        "present": "{{ default(inputs.vlan_vni, 'absent') }}",
        "name": "{{ default(inputs.vlan_name, 'absent') }}",
        "enabled": "{{ default(inputs.vlan_enabled, 'absent') }}",
    }


def parent_with(ws, world, child_inputs, child_schema=SCHEMA, **parent_inputs):
    publish(
        ws,
        "a",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=child_schema,
        outputs=child_outputs(),
        user=world["designer"],
    )
    return publish(
        ws,
        "b",
        [node("s", "subworkflow", workflow="a", inputs=child_inputs)],
        [],
        inputs=schema(["vlan_id"], vlan_id=INT, vlan_vni=INT, vlan_name=STR, vlan_enabled=BOOL),
        outputs={"r": "{{ steps.s.output }}"},
        user=world["designer"],
    )


NULLS = {
    "vlan_id": "{{ inputs.vlan_id }}",
    "vlan_vni": "{{ default(inputs.vlan_vni, null) }}",
    "vlan_name": "{{ default(inputs.vlan_name, null) }}",
    "vlan_enabled": "{{ default(inputs.vlan_enabled, null) }}",
}


def run_b(ws, world, inputs, **kw):
    v = parent_with(ws, world, NULLS, **kw)
    return drain(launch(ws, v, world["designer"], inputs))


def test_optional_fields_at_null_are_omitted_in_the_called_workflow(ws, world):
    run = run_b(ws, world, {"vlan_id": 10})
    assert run.status == "succeeded", run.error
    child = WorkflowRun.objects.get(parent_step__run=run)
    assert all(k not in child.inputs for k in OPT)  # integer, string and boolean alike
    assert run.outputs["r"] == {"present": "absent", "name": "absent", "enabled": "absent"}


def test_omitted_and_null_give_the_same_result(ws, world):
    parent_with(ws, world, NULLS)
    omitted = parent_with(ws, world, {"vlan_id": "{{ inputs.vlan_id }}"})
    run = drain(launch(ws, omitted, world["designer"], {"vlan_id": 10}))
    assert run.status == "succeeded"
    assert run.outputs["r"] == {"present": "absent", "name": "absent", "enabled": "absent"}


def test_false_zero_and_empty_string_are_values_not_omissions(ws, world):
    inputs = {"vlan_id": 0, "vlan_vni": 0, "vlan_name": "", "vlan_enabled": False}
    run = run_b(ws, world, inputs)
    assert run.status == "succeeded", run.error
    child = WorkflowRun.objects.get(parent_step__run=run)
    assert child.inputs == {"vlan_id": 0, "vlan_vni": 0, "vlan_name": "", "vlan_enabled": False}
    assert drop_null_optionals(SCHEMA, inputs) == inputs


def test_a_required_field_at_null_is_an_explicit_error(ws, world):
    publish(
        ws,
        "a",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=SCHEMA,
        outputs=child_outputs(),
        user=world["designer"],
    )
    v = publish(
        ws,
        "b",
        [node("s", "subworkflow", workflow="a", inputs={"vlan_id": "{{ default(inputs.n, null) }}"})],
        [],
        inputs=schema(n=INT),
        outputs={},
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], {}))
    assert run.status == "failed"
    assert "vlan_id" in run.error["message"] and "obligatoire" in run.error["message"]


def test_a_wrong_type_on_an_optional_field_keeps_the_current_message(ws, world):
    publish(
        ws,
        "a",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=SCHEMA,
        outputs={},
        user=world["designer"],
    )
    v = publish(
        ws,
        "b",
        [node("s", "subworkflow", workflow="a", inputs={"vlan_id": 1, "vlan_vni": "{{ default(inputs.t, 'abc') }}"})],
        [],
        inputs=schema(t={}),
        outputs={},
        user=world["designer"],
    )
    run = drain(launch(ws, v, world["designer"], {"t": "abc"}))
    assert run.status == "failed"
    assert "champ « vlan_vni » : type attendu : integer" in run.error["message"]


def test_an_explicitly_nullable_schema_is_still_accepted(ws, world):
    nullable = schema(
        ["vlan_id"], vlan_id=INT, vlan_vni={"type": ["integer", "null"]}, vlan_name=STR, vlan_enabled=BOOL
    )
    assert validate_inputs_against(nullable, {"vlan_id": 1, "vlan_vni": None}) == {}
    assert (
        validate_inputs_against(schema(["x"], x={"type": ["integer", "null"]}), {"x": None}) == {}
    )  # nullable required
    run = run_b(ws, world, {"vlan_id": 10}, child_schema=nullable)
    assert run.status == "succeeded", run.error


def test_every_entry_point_applies_the_rule():
    assert (
        validate_inputs_against(SCHEMA, {"vlan_id": 1, "vlan_vni": None, "vlan_name": None, "vlan_enabled": None}) == {}
    )
    assert validate_inputs_against(SCHEMA, {"vlan_id": None}) == {"vlan_id": "Ce champ est obligatoire."}
    assert validate_inputs_against(SCHEMA, {"vlan_id": 1, "vlan_vni": "abc"}) == {
        "vlan_vni": "champ « vlan_vni » : type attendu : integer"
    }
    assert validate_inputs_against(SCHEMA, {}) == {"vlan_id": "Ce champ est obligatoire."}


def test_a_null_optional_takes_its_default_like_an_omitted_one():
    s = schema(["a"], a=INT, b={"type": "integer", "default": 5})
    assert with_defaults(s, {"a": 1, "b": None}) == with_defaults(s, {"a": 1}) == {"a": 1, "b": 5}
    assert with_defaults(s, {"a": 1, "b": 0}) == {"a": 1, "b": 0}


def test_inputs_given_as_an_expression_is_refused_by_validation_not_by_a_crash(ws, world):
    publish(
        ws,
        "a",
        [node("e", action="echo", inputs={"value": "x"})],
        [],
        inputs=SCHEMA,
        outputs={},
        user=world["designer"],
    )
    for kind, extra in (("subworkflow", {"workflow": "a"}), ("action", {"action": "echo"})):
        g = {
            "inputs": schema(),
            "nodes": [node("go", kind, inputs="{{ steps.prep.output.entrees }}", **extra)],
            "edges": [],
            "outputs": {},
        }
        issues = validate_graph(g, ws, "parent")
        assert [(i["node"], i["code"], i["level"]) for i in issues] == [("go", "bad_inputs_shape", "error")]


def test_nullable_workflow_input_is_compatible_with_an_optional_form_field():
    form_schema = schema(["vlan_id"], vlan_id=INT, vlan_vni={"type": ["integer", "null"]})
    definition = {
        "sections": [
            {
                "key": "s",
                "title": "S",
                "fields": [
                    {"key": "vlan_id", "type": "number", "label": "VLAN"},
                    {"key": "vlan_vni", "type": "number", "label": "VNI"},
                ],
            }
        ]
    }
    problems = formengine.check_against_inputs(definition, form_schema, None)
    assert not any("attend" in m for m in problems.values()), problems
    wrong = schema(["vlan_id"], vlan_id=INT, vlan_vni={"type": ["boolean", "null"]})
    assert any("attend" in m for m in formengine.check_against_inputs(definition, wrong, None).values())
