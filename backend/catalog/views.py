from django.http import Http404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy.permissions import EDIT, VIEW, get_workspace, has_perm

from .models import Action
from .serializers import ActionSerializer


class ActionListView(ListAPIView):
    serializer_class = ActionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = (
            Action.objects.filter(workspace=ws)
            .exclude(slug__startswith="__")
            .select_related("owner")
            .prefetch_related("versions")
        )
        # ADR 0025 : les propositions d'assistant n'apparaissent que sur demande (?status=proposed) et seulement pour qui peut modifier
        if self.request.query_params.get("status") == "proposed" and has_perm(self.request.user, ws, EDIT):
            qs = qs.filter(status="proposed")
        else:
            qs = qs.exclude(status="proposed")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(description__icontains=q)
        domain = self.request.query_params.get("domain")
        if domain:
            qs = qs.filter(domain=domain)
        return qs.order_by("name", "id")


class ActionDetailView(RetrieveAPIView):
    serializer_class = ActionSerializer
    lookup_field = "slug"

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        return Action.objects.filter(workspace=ws)

    def get_object(self):
        obj = self.get_queryset().filter(slug=self.kwargs["slug"]).first()
        if obj is None or (obj.status == "proposed" and not has_perm(self.request.user, obj.workspace, EDIT)):
            raise Http404()
        return obj


class IntegrationCatalogView(APIView):
    """The built-in integrations (static descriptions: no workspace data, no secret)."""

    def get(self, request, workspace):
        from . import integrations

        get_workspace(request.user, workspace, VIEW)
        return Response({"results": integrations.public()})
