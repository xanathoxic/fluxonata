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


def with_defaults(schema: dict, inputs: dict) -> dict:
    """Inputs completed with the schema's `default` of every omitted top-level property (what the launch form does for a
    person): API, schedules and webhooks get the same behaviour. Explicit values, even null, are never replaced."""
    out = dict(inputs)
    for name, spec in (schema.get("properties") or {}).items():
        if name not in out and isinstance(spec, dict) and "default" in spec:
            out[name] = spec["default"]
    return out


def validate_inputs_against(schema: dict, inputs: dict) -> dict[str, str]:
    """Return {field: message} (empty when valid)."""
    validator = jsonschema.Draft202012Validator(schema)
    errors = {}
    for err in sorted(validator.iter_errors(inputs), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or "_"
        if err.validator == "required":
            for name in err.validator_value:
                if name not in inputs:
                    errors[name] = "Ce champ est obligatoire."
        else:
            errors.setdefault(field, explain_validation_error(err))
    return errors
