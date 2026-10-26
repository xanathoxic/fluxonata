import jsonschema

from runs.runners import RunOutcome
from workflows.validation import explain_validation_error

from . import service


class AiRunner:
    """Executes an AI step inside the worker. The output is untrusted: it must satisfy the declared schema."""

    def __init__(self, execution, provider=None):
        self.execution, self.provider = execution, provider

    def run(self, version, inputs, timeout, cancel=None, env=None):
        ex = self.execution
        schema = inputs["output_schema"]
        try:
            out = service.run_prompt(
                ex.workspace,
                ex.requested_by,
                "ai_step",
                data=str(inputs["prompt"]),
                context={"output_schema": schema},
                provider_name=inputs.get("provider"),
                provider=self.provider,
            )
            jsonschema.validate(out, schema)
        except service.AiRefused as exc:
            return RunOutcome(False, error={"code": exc.code, "message": exc.message})
        except jsonschema.ValidationError as exc:
            return RunOutcome(
                False,
                error={
                    "code": "MODEL_OUTPUT_INVALID",
                    "message": f"La réponse du modèle ne respecte pas le schéma attendu — {explain_validation_error(exc)}",
                },
            )
        return RunOutcome(True, result=out)
