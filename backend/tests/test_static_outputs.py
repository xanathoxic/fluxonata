"""Les sorties garanties de chaque type d'étape, telles que l'éditeur les montre (`staticOutputs.json`), sont celles que le validateur accepte."""

import json

from workflows import static_outputs
from workflows.validation import NODE_TYPES, STATIC_OUTPUTS


def test_the_editor_copy_is_the_validators_list_not_a_drifting_duplicate():
    on_disk = json.loads(static_outputs.TARGET.read_text(encoding="utf-8"))
    assert on_disk == static_outputs.document(), (
        "frontend/src/workflow/staticOutputs.json est périmé : cd backend && ../.venv/bin/python manage.py export_static_outputs"
    )
    assert (
        static_outputs.TARGET.read_text(encoding="utf-8") == static_outputs.render()
    )  # byte for byte, so the diff is empty after regeneration


def test_every_field_the_validator_knows_is_listed_with_its_type():
    doc = static_outputs.document()
    for node_type, fields in STATIC_OUTPUTS.items():
        assert set(doc[node_type]) == set(fields), node_type
    assert doc["http"]["status"] == "integer" and doc["http"]["body"] is None  # a free-form value has no type
    assert set(doc["wait_resume"]) == {"resumed", "payload"}
    assert set(doc["ssh"]) >= {"status", "results", "failed_step", "error_type", "uncertain", "hops"}


def test_the_types_with_a_dynamic_output_are_not_in_the_static_list():
    # action / script / ai / transform / subworkflow outputs depend on their own configuration: the editor computes them, the validator reads their contract
    assert not {"action", "script", "ai", "transform", "subworkflow"} & set(STATIC_OUTPUTS)
    assert set(STATIC_OUTPUTS) <= set(NODE_TYPES)
