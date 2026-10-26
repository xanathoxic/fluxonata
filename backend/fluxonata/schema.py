"""OpenAPI generation tweaks: untyped APIViews are documented as generic JSON objects (instead of being dropped),
and path parameters default to strings."""

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers


class AnyObject(serializers.Serializer):
    """A free-form JSON object (the endpoint is documented in docs/API.md)."""


class FluxonataSchema(AutoSchema):
    def _get_serializer(self):
        view = self.view
        if getattr(view, "serializer_class", None) is None and not hasattr(view, "get_serializer"):
            return AnyObject()
        return super()._get_serializer()

    def get_override_parameters(self):
        params = list(super().get_override_parameters())
        known = {p.name for p in params}
        import re

        path = getattr(self, "path", "") or ""
        for name in re.findall(r"{(\w+)}", path):
            if name not in known:
                params.append(OpenApiParameter(name, type=str, location=OpenApiParameter.PATH))
        return params
