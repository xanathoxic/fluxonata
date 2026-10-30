"""Un paramètre « liste » ou « objet » saisi comme texte JSON est une liste / un objet (et non du texte)."""

from tests.wf_helpers import drain, launch, make_action, node, publish, schema
from workflows.validation import validate

ITEM = schema(["subject"], subject={"type": "string"})


def run_with(world, lines, subject='Re: "Panne" urgente'):
    ws = world["ws_a"]
    make_action(ws, "lines")
    v = publish(
        ws,
        "courrier",
        [node("rapport", action="lines", inputs={"lines": lines})],
        [],
        inputs=ITEM,
        outputs={"n": "{{ steps.rapport.output.n }}", "joined": "{{ steps.rapport.output.joined }}"},
        user=world["designer"],
    )
    return drain(launch(ws, v, world["designer"], {"subject": subject}))


def test_json_text_for_a_list_parameter_is_a_list_and_templates_are_resolved_per_element(world):
    text = '["Objet : {{ inputs.subject }}", "Fixe"]'  # what a text box in the editor stored
    run = run_with(world, text)
    assert run.status == "succeeded", run.error
    assert run.outputs == {
        "n": 2,
        "joined": 'Objet : Re: "Panne" urgente | Fixe',
    }  # a quote in the data cannot break the JSON


def test_a_real_list_and_a_whole_template_still_work(world):
    run = run_with(world, ["Objet : {{ inputs.subject }}"])
    assert run.status == "succeeded" and run.outputs["n"] == 1


def test_text_that_is_not_json_is_reported_when_validating_not_at_run_time(world):
    ws = world["ws_a"]
    make_action(ws, "lines")
    graph = {
        "inputs": ITEM,
        "nodes": [node("rapport", action="lines", inputs={"lines": "juste une phrase"})],
        "edges": [],
        "outputs": {},
    }
    problems = [i for i in validate(graph, ws, "x") if i["code"] == "bad_param_value"]
    assert problems and "JSON" in problems[0]["message"]
    graph["nodes"][0]["inputs"]["lines"] = '["ok", "{{ inputs.subject }}"]'
    assert not [i for i in validate(graph, ws, "x") if i["level"] == "error"]
