import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fluxonata.settings")
application = get_wsgi_application()

# Une API ne démarre pas avec un ORCH_ROLES invalide pour elle (un rôle de worker, une faute de frappe) : échec franc, pas de 404 mystérieux (AR2.3).
from django.conf import settings  # noqa: E402
from django.urls import get_resolver  # noqa: E402

from fluxonata.apiroles import parse_api_roles  # noqa: E402

parse_api_roles(settings.ORCH_ROLES)
_ = get_resolver().url_patterns  # charge les routes dès le démarrage
