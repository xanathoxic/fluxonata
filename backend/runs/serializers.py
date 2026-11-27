import jsonschema
from rest_framework import serializers

from workflows.validation import explain_validation_error

from .models import Attempt, Execution, ExecutionEvent


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExecutionEvent
        fields = ["seq", "type", "message", "created_at"]


class AttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attempt
        fields = ["number", "status", "started_at", "finished_at", "exit_code", "logs"]


class ExecutionSerializer(serializers.ModelSerializer):
    action = serializers.SerializerMethodField()
    environment = serializers.CharField(source="environment.kind")
    requested_by = serializers.CharField(source="requested_by.username", default=None)
    status_label = serializers.CharField(source="get_status_display")
    runner_name = serializers.CharField(source="runner.name", default=None)

    class Meta:
        model = Execution
        fields = [
            "id",
            "action",
            "environment",
            "requested_by",
            "status",
            "status_label",
            "inputs",
            "result",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "attempt_count",
            "runner_name",
        ]

    def get_action(self, obj) -> dict:
        v = obj.action_version
        return {"slug": v.action.slug, "name": v.action.name, "version": v.version, "output_schema": v.output_schema}


class ExecutionDetailSerializer(ExecutionSerializer):
    events = EventSerializer(many=True, read_only=True)
    attempts = AttemptSerializer(many=True, read_only=True)

    class Meta(ExecutionSerializer.Meta):
        fields = ExecutionSerializer.Meta.fields + ["events", "attempts"]


class RunRequestSerializer(serializers.Serializer):
    action = serializers.SlugField()
    environment = serializers.ChoiceField(choices=["dev", "test", "prod"])
    inputs = serializers.JSONField()
    connections = serializers.DictField(child=serializers.CharField(max_length=60), required=False, default=dict)

    def validate_inputs(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Les entrées doivent être un objet.")
        return value


def _allows_null(spec: object) -> bool:
    kind = spec.get("type") if isinstance(spec, dict) else None
    return kind == "null" or (isinstance(kind, list) and "null" in kind)


def drop_null_optionals(schema: dict, inputs: dict) -> dict:
    """Inputs without the top-level optional fields that are null: `null` on an optional field means « omitted » (what `default(inputs.x, null)` produces
    when a step hands a value it does not have). Required fields are kept as they are (a null there stays an error); `false`, `0` and `""` are values."""
    required = set(schema.get("required") or [])
    return {k: v for k, v in inputs.items() if not (v is None and k not in required)}


def with_defaults(schema: dict, inputs: dict) -> dict:
    """Inputs completed with the schema's `default` of every omitted top-level property (what the launch form does for a
    person): API, schedules and webhooks get the same behaviour. An optional field at null counts as omitted (it takes its default too);
    any other explicit value is never replaced."""
    out = drop_null_optionals(schema, inputs)
    for name, spec in (schema.get("properties") or {}).items():
        if name not in out and isinstance(spec, dict) and "default" in spec:
            out[name] = spec["default"]
    return out


def validate_inputs_against(schema: dict, inputs: dict) -> dict[str, str]:
    """Return {field: message} (empty when valid). An optional field at null is treated as omitted; a required one at null is « obligatoire »."""
    inputs = drop_null_optionals(schema, inputs)
    errors = {}
    props = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name in inputs and inputs[name] is None and not _allows_null(props.get(name)):
            errors[name] = "Ce champ est obligatoire."
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(inputs), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or "_"
        if err.validator == "required":
            for name in err.validator_value:
                if name not in inputs:
                    errors[name] = "Ce champ est obligatoire."
        else:
            errors.setdefault(field, explain_validation_error(err))
    return errors


def first_problem(problems: dict[str, str]) -> str:
    """One readable sentence for the first problem of `validate_inputs_against` (used where a step, not a form, receives the error)."""
    field, message = next(iter(problems.items()))
    return message if message.startswith("champ « ") else f"champ « {field} » : {message}"
