"""The output fields every step type is guaranteed to produce, as the editor shows them (generated file `frontend/src/workflow/staticOutputs.json`).

One source: `workflows.validation.STATIC_OUTPUTS` (what the validator accepts in `steps.<id>.output.<field>`). The editor reads the generated copy so that its
output lists (tab « Sorties », variable pickers) cannot drift from the validator; a test fails when the copy is stale."""

import json
from pathlib import Path

from .validation import STATIC_OUTPUTS, WEBHOOK_WAIT_OUTPUTS

TARGET = Path(__file__).resolve().parents[2] / "frontend" / "src" / "workflow" / "staticOutputs.json"


def document() -> dict:
    """`{step type: {field: JSON-schema type or null}}`, plus `wait_resume` for a wait step that resumes by webhook or form."""
    rows = {**STATIC_OUTPUTS, "wait_resume": WEBHOOK_WAIT_OUTPUTS}
    return {t: {k: (v or {}).get("type") for k, v in fields.items()} for t, fields in sorted(rows.items())}


def render() -> str:
    return json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if (
    __name__ == "__main__"
):  # pragma: no cover - regeneration: cd backend && ../.venv/bin/python -m workflows.static_outputs  (after django.setup via manage.py)
    TARGET.write_text(render(), encoding="utf-8")
