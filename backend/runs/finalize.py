"""What happens to an attempt's outcome before it is recorded. Shared by the built-in worker and by the runner API, so a
remote runner is never trusted with more than the local worker is: the platform masks secrets, extracts artifacts and
validates the output against the contract itself."""

import jsonschema

from vault.injection import all_forms, mask_json, mask_text
from workflows.validation import explain_validation_error

from . import services
from .artifacts import discard_artifacts, extract_artifacts, store_artifacts
from .runners import RunOutcome

MIN_FRAGMENT = 4  # same floor as for whole secrets (`resolve_env` keeps only values of 4+ characters)
MIN_FRAGMENT_DERIVED = (
    8  # a fragment of an encoded form (base64 alphabet...) coincides with ordinary text far more easily
)


def mask_cut_fragment(text: str, secrets: list[str]) -> str:
    """Logs are cut to their last N bytes BEFORE they reach the platform (`runners._parse`, the runner agent, `JobCompleteView`),
    so the cut can slice a secret: its end then opens the text and is no longer a full match for `mask_text`. Masks such a
    leading fragment: a proper suffix of a secret (MIN_FRAGMENT characters or more) or of one of its supported forms
    (MIN_FRAGMENT_DERIVED or more). A character cut in two by the byte cut shows up as U+FFFD and is skipped before matching."""
    lead = len(text) - len(text.lstrip("\ufffd"))
    rest = text[lead:]
    raw = {s for s in secrets if s}
    for form in all_forms(secrets):
        floor = MIN_FRAGMENT if form in raw else MIN_FRAGMENT_DERIVED
        for start in range(1, len(form) - floor + 1):  # longest proper suffix first
            if rest.startswith(form[start:]):
                return "***" + rest[len(form) - start :]
    return text


def finalize_attempt(execution, attempt: int, outcome: RunOutcome, to_mask: list[str]) -> bool:
    """Mask secrets, extract artifacts, validate the output schema, then record the attempt (fenced by `attempt`).
    Returns False when the attempt was no longer the live one (its result is ignored)."""
    version = execution.action_version
    outcome.logs = mask_cut_fragment(mask_text(outcome.logs, to_mask), to_mask)
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
    stored = []
    if outcome.ok and pending_artifacts and services.attempt_is_live(execution.id, attempt):
        # a late answer (fenced-out attempt, cancelled or finished run) must not add files to a run it no longer owns
        stored = store_artifacts(execution, pending_artifacts)
    accepted = services.complete_attempt(
        execution.id,
        attempt,
        ok=outcome.ok,
        result=outcome.result,
        error=error,
        logs=outcome.logs,
        exit_code=outcome.exit_code,
        cancelled=outcome.cancelled,
    )
    if not accepted and stored:  # lost the race between the check above and the recording: take the files back
        discard_artifacts(stored)
    return accepted
