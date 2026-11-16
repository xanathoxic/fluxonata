from django.apps import AppConfig


class TenancyConfig(AppConfig):
    name = "tenancy"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # The outgoing-connection guard (`core.netsec`) depends on no application: the list of hosts an administrator allowed in the web
        # interface is given to it from here.
        from core import netsec

        from .models import EgressHost

        netsec.set_managed_source(lambda: EgressHost.objects.values_list("host", flat=True))
