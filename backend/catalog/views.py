from django.http import Http404
from rest_framework.generics import ListAPIView, RetrieveAPIView

from tenancy.permissions import VIEW, get_workspace

from .models import Action
from .serializers import ActionSerializer


class ActionListView(ListAPIView):
    serializer_class = ActionSerializer

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        qs = Action.objects.filter(workspace=ws).exclude(slug__startswith="__").select_related("owner")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(description__icontains=q)
        domain = self.request.query_params.get("domain")
        if domain:
            qs = qs.filter(domain=domain)
        return qs.order_by("name")


class ActionDetailView(RetrieveAPIView):
    serializer_class = ActionSerializer
    lookup_field = "slug"

    def get_queryset(self):
        ws = get_workspace(self.request.user, self.kwargs["workspace"], VIEW)
        return Action.objects.filter(workspace=ws)

    def get_object(self):
        obj = self.get_queryset().filter(slug=self.kwargs["slug"]).first()
        if obj is None:
            raise Http404()
        return obj
