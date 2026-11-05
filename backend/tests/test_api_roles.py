"""AR2.3 — rôles d'un process d'API : `ui`, `ingest`, `runner-gw` (ORCH_ROLES). Le défaut (tous) est strictement l'ancien comportement."""

import importlib
import sys
import types
import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.test.utils import override_settings
from django.urls import Resolver404, clear_url_caches, get_resolver, resolve

from fluxonata import apiroles
from fluxonata.apiroles import API_ROLES, parse_api_roles, role_of
from fluxonata.urls import APP_URLCONFS, build_urlpatterns

HOOK = f"/api/v1/hooks/{uuid.uuid4()}/"
PUBLIC = [
    HOOK,
    "/api/v1/hooks/resume/abc/",
    "/api/v1/forms/tok/",
    "/api/v1/forms/tok/lookup/champ/",
    "/api/v1/resume-forms/tok/",
]
RUNNER = [
    "/api/v1/runner/me/",
    "/api/v1/runner/jobs/request/",
    f"/api/v1/runner/jobs/{uuid.uuid4()}/heartbeat/",
    f"/api/v1/runner/jobs/{uuid.uuid4()}/complete/",
]
PRIVATE = [
    "/api/v1/auth/login/",
    "/api/v1/auth/csrf/",
    "/api/v1/workspaces/team-a/actions/",
    "/api/v1/workspaces/team-a/webhooks/",  # la GESTION des webhooks est privée ; seule la réception est publique
    "/api/v1/workspaces/team-a/forms/",
    "/api/v1/platform/runners/",  # l'ADMINISTRATION des runners est privée ; seul leur protocole est « runner-gw »
    "/api/v1/scim/v2/Users",
    "/api/v1/metrics/",
    "/api/v1/schema/",
]
PROBES = ["/api/v1/health/live/", "/api/v1/health/ready/"]


_confs: dict = {}


def served(roles, path):
    key = tuple(roles)
    if (
        key not in _confs
    ):  # `resolve` met le résolveur en cache : il lui faut un objet hachable (un module), pas une liste
        _confs[key] = types.ModuleType("_urlconf_" + "_".join(key))
        _confs[key].urlpatterns = build_urlpatterns(roles)
    try:
        resolve(path, urlconf=_confs[key])
        return True
    except Resolver404:
        return False


# --- analyse de la valeur ---------------------------------------------------------------------------------------------
def test_empty_means_every_role_and_aliases_are_accepted():
    assert parse_api_roles("") == parse_api_roles(None) == API_ROLES
    assert parse_api_roles("api-ui, API-INGEST,ingest") == ("ui", "ingest")
    assert parse_api_roles("gw") == ("runner-gw",)


@pytest.mark.parametrize("bad", ["exec", "worker-aux", "admin", "ui,aux"])
def test_unknown_or_worker_roles_are_refused_with_the_valid_list(bad):
    with pytest.raises(ImproperlyConfigured, match="Valeurs possibles : ui, ingest, runner-gw"):
        parse_api_roles(bad)


# --- ce que chaque rôle sert ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", PUBLIC + RUNNER + PRIVATE + PROBES)
def test_the_default_serves_everything_exactly_as_before(path):
    assert served(API_ROLES, path), path


@pytest.mark.parametrize("path", PUBLIC)
def test_ingest_serves_only_the_public_entry_points(path):
    assert served(("ingest",), path)
    assert not any(served(("ingest",), p) for p in RUNNER + PRIVATE)


def test_runner_gw_serves_only_the_runner_protocol():
    assert all(served(("runner-gw",), p) for p in RUNNER)
    assert not any(served(("runner-gw",), p) for p in PUBLIC + PRIVATE)


def test_ui_serves_the_private_surface_and_none_of_the_public_or_runner_ones():
    assert all(served(("ui",), p) for p in PRIVATE)
    assert not any(served(("ui",), p) for p in PUBLIC + RUNNER)


@pytest.mark.parametrize("roles", [("ui",), ("ingest",), ("runner-gw",), ("ui", "ingest"), ("ingest", "runner-gw")])
def test_every_role_serves_the_health_probes(roles):
    assert all(served(roles, p) for p in PROBES)


def test_combined_roles_serve_the_union():
    assert all(served(("ui", "ingest"), p) for p in PUBLIC + PRIVATE) and not any(
        served(("ui", "ingest"), p) for p in RUNNER
    )


def test_every_route_belongs_to_exactly_one_role_and_none_is_lost():
    """Garde-fou de complétude : une route ajoutée plus tard est servie par un rôle (jamais perdue), et par un seul (jamais publiée par mégarde)."""
    full = []
    for app in APP_URLCONFS:
        full += [str(p.pattern) for p in importlib.import_module(f"{app}.urls").urlpatterns]
    by_role = {r: [] for r in API_ROLES}
    for route in full:
        by_role[role_of(route)].append(route)
    assert sum(len(v) for v in by_role.values()) == len(full) and len(full) > 150
    for role in API_ROLES:
        got = [
            str(p.pattern)
            for p in importlib.import_module("fluxonata.urls").build_urlpatterns((role,))[-1].url_patterns
        ]
        assert sorted(got) == sorted(by_role[role]), role
    assert (
        len(by_role["ingest"]) == 6 and len(by_role["runner-gw"]) == 4
    )  # si ces nombres changent, c'est une décision à relire


def test_public_prefixes_never_capture_a_private_management_route():
    for app in APP_URLCONFS:
        for p in importlib.import_module(f"{app}.urls").urlpatterns:
            route = str(p.pattern)
            if route.startswith("workspaces/") or route.startswith("platform/"):
                assert role_of(route) == "ui", route


# --- à travers HTTP : une route non servie répond 404 ---------------------------------------------------------------------
@pytest.fixture
def conf_for(settings):
    created = []

    def make(*roles):
        name = "tests._urlconf_" + "_".join(r.replace("-", "") for r in roles)
        mod = types.ModuleType(name)
        mod.urlpatterns = build_urlpatterns(roles)
        sys.modules[name] = mod
        created.append(name)
        clear_url_caches()
        return override_settings(ROOT_URLCONF=name)

    yield make
    for n in created:
        sys.modules.pop(n, None)
    clear_url_caches()


@pytest.mark.django_db
def test_a_route_a_role_does_not_serve_answers_404_and_a_served_one_is_reached(conf_for):
    with conf_for("ingest"):
        c = Client()
        assert c.get("/api/v1/workspaces/team-a/actions/").status_code == 404  # l'API privée n'existe pas ici
        assert c.get("/api/v1/auth/csrf/").status_code == 404
        assert (
            c.post(HOOK, data="{}", content_type="application/json").status_code == 401
        )  # la réception est atteinte (signature absente)
        assert c.get("/api/v1/health/live/").status_code == 200
    with conf_for("ui"):
        c = Client()
        assert (
            c.post(HOOK, data="{}", content_type="application/json").status_code == 404
        )  # la réception publique n'existe pas côté interface
        assert c.get("/api/v1/auth/csrf/").status_code == 200
    with conf_for("runner-gw"):
        assert Client().post(
            "/api/v1/runner/jobs/request/", data="{}", content_type="application/json"
        ).status_code in (401, 403)
        assert Client().get("/api/v1/auth/csrf/").status_code == 404


def test_an_api_refuses_to_start_with_an_invalid_role_but_a_worker_may_carry_its_own(settings):
    """`ORCH_ROLES` est partagé : « exec » est valide pour un worker, invalide pour une API. Le contrôle strict est dans wsgi.py ; le chargement des routes, lui,
    ne doit pas planter sur une valeur de worker (les contrôles système de `manage.py run_worker` le font : défaut trouvé sur la pile par rôles)."""
    from django.core.management import call_command

    settings.ORCH_ROLES = "exec"
    importlib.reload(
        importlib.import_module("fluxonata.urls")
    )  # un process worker : toutes les routes, aucune exception
    call_command("check")  # ce que fait `manage.py run_worker` au démarrage
    with pytest.raises(ImproperlyConfigured, match="rôle d'API inconnu"):
        importlib.reload(importlib.import_module("fluxonata.wsgi"))  # une API : échec franc
    settings.ORCH_ROLES = ""
    importlib.reload(importlib.import_module("fluxonata.urls"))
    importlib.reload(importlib.import_module("fluxonata.wsgi"))  # et le défaut démarre
    assert get_resolver().url_patterns and apiroles.API_ROLES == API_ROLES
