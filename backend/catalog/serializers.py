from rest_framework import serializers

from .models import Action, ActionVersion


class ActionVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionVersion
        fields = [
            "id",
            "version",
            "runtime",
            "input_schema",
            "output_schema",
            "license",
            "side_effects",
            "idempotent",
            "network_policy",
            "limits",
            "examples",
            "source_digest",
            "created_at",
        ]


class ActionSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()
    owner = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Action
        fields = [
            "slug",
            "name",
            "description",
            "domain",
            "folder",
            "tags",
            "owner",
            "status",
            "deprecation_reason",
            "latest_version",
        ]

    def get_latest_version(self, obj) -> dict | None:
        version = obj.latest_version()
        return ActionVersionSerializer(version).data if version else None
