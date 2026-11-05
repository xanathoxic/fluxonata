from rest_framework import serializers

from . import contract
from .models import Action, ActionVersion


class ActionVersionSerializer(serializers.ModelSerializer):
    contract = serializers.SerializerMethodField()

    def get_contract(self, obj) -> dict:
        return contract.contract(obj.action.description, obj.input_schema, obj.output_schema, obj)

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
            "contract",
        ]


class ActionSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()
    contract_complete = serializers.SerializerMethodField()
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
            "contract_complete",
        ]

    def get_contract_complete(self, obj) -> bool | None:
        v = obj.latest_version()
        return not contract.problems(obj.description, v.input_schema, v.output_schema) if v else None

    def get_latest_version(self, obj) -> dict | None:
        version = obj.latest_version()
        if not version:
            return None
        # AR1.5 : un `ActionVersionSerializer(version).data` par action reconstruit tous ses champs à chaque fois (mesuré : l'essentiel des
        # 17 ms de GET /actions/ pour 62 actions ; la vérification du contrat, elle, coûte 0,2 ms). Une instance par sérialiseur de liste suffit.
        child = self.__dict__.get("_version_serializer")
        if child is None:
            child = self.__dict__["_version_serializer"] = ActionVersionSerializer(context=self.context)
        return child.to_representation(version)
