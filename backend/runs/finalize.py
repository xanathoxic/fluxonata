"""What happens to an attempt's outcome before it is recorded. Shared by the built-in worker and by the runner API, so a
remote runner is never trusted with more than the local worker is: the platform masks secrets, extracts artifacts and
validates the output against the contract itself."""

import jsonschema

from vault.injection import mask_json, mask_text
from workflows.validation import explain_validation_error

from . import services
from .artifacts import extract_artifacts, store_artifacts
from .runners import RunOutcome


def finalize_attempt(execution, attempt: int, outcome: RunOutcome, to_mask: list[str]) -> bool:
    """Mask secrets, extract artifacts, validate the output schema, then record the attempt (fenced by `attempt`).
    Returns False when the attempt was no longer the live one (its result is ignored)."""
    version = execution.action_version
    outcome.logs = mask_text(outcome.logs, to_mask)
    outcome.result = mask_json(outcome.result, to_mask)
    if outcome.error:
        outcome.error = mask_json(outcome.error, to_mask)
    pending_artifacts = []
    if outcome.ok:
        try:
            outcome.result, pending_artifacts = extract_artifacts(outcome.result)
        except ValueError as exc:
            outcome.ok, outcome.error = False, {"code": "ARTIFACT_INVALID", "message": str(exc)}
    if outcome.ok:
        try:
            jsonschema.validate(outcome.result, version.output_schema)
        except jsonschema.ValidationError as exc:
            outcome.ok = False
            outcome.error = {
                "code": "OUTPUT_INVALID",
                "message": f"Le résultat ne respecte pas le contrat de sortie — {explain_validation_error(exc)}",
            }
    error = None
    if not outcome.ok:
        error = dict(outcome.error or {"code": "ERROR", "message": "Échec."})
        error.setdefault("next_step", "Consultez les journaux, corrigez les entrées puis relancez l'action.")
    if outcome.ok and pending_artifacts:
        store_artifacts(execution, pending_artifacts)
    return services.complete_attempt(
        execution.id,
        attempt,
        ok=outcome.ok,
        result=outcome.result,
        error=error,
        logs=outcome.logs,
        exit_code=outcome.exit_code,
        cancelled=outcome.cancelled,
    )
