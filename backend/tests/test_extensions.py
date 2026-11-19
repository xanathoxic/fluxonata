"""Extensions (ADR 0030) : manifeste vérifié, chargement qui ne plante jamais, menu filtré par droits côté serveur, fichiers d'interface confinés."""

import copy

import pytest
from django.conf import settings

from extensions import EXTENSION_API
from extensions.loader import Extension, discover
from extensions.manifest import PERMISSIONS, ManifestError, parse_manifest
from extensions.urls import plugin_routes
from tenancy.permissions import ALL, CATALOG
from tests.conftest import client_for
from tests.sample_extension.plugin import MANIFEST

BASE = "/api/v1/workspaces/team-a/extensions/"


@pytest.fixture
def sample(settings):
    ext = Extension(parse_manifest(MANIFEST), "tests.sample_extension")
    settings.EXTENSIONS = [ext]
    settings.EXTENSIONS_REFUSED = []
    return ext


def bad(**over):
    m = copy.deepcopy(MANIFEST)
    m.update(over)
    return m


def test_a_valid_manifest_is_accepted_and_frozen():
    m = parse_manifest(MANIFEST)
    assert (m.id, m.version, m.ui_module) == ("exemple", "1.0.0", "index.js")
    assert [e.id for e in m.menu] == ["accueil", "reglages"] and m.menu[0].icon == "puzzle"


@pytest.mark.parametrize(
    "over",
    [
        {"id": "Mauvais Id"},
        {"id": "x"},
        {"version": "1.0"},
        {"api": EXTENSION_API + 1},
        {"name": ""},
        {"license": ""},
        {"inconnue": 1},
        {"menu": [{"id": "a", "label": "A", "path": "/../x"}]},
        {"menu": [{"id": "a", "label": "A", "group": "Autre"}]},
        {"menu": [{"id": "a", "label": "A", "permission": "tout"}]},
        {"menu": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]},
        {"menu": [{"id": "a", "label": "x" * 41}]},
        {"menu": [{"id": f"m{i}", "label": "M"} for i in range(9)]},
        {"ui": {"module": "../evil.js"}},
        {"ui": {"module": "index.py"}},
        {"ui": {}},  # un menu sans page
    ],
)
def test_a_faulty_manifest_is_refused_with_a_reason(over):
    with pytest.raises(ManifestError):
        parse_manifest(bad(**over))


def test_a_manifest_without_menu_or_page_is_valid():
    assert parse_manifest(bad(menu=[], ui={})).menu == ()


def test_the_permission_list_follows_tenancy():
    assert set(PERMISSIONS) == ALL | {CATALOG}


def test_discovery_never_fails_the_startup():
    found, refused = discover("tests.sample_extension, absent.paquet, tests, tests.sample_extension, 9mauvais,")
    assert [e.manifest.id for e in found] == ["exemple"]
    reasons = {r.name: r.reason for r in refused}
    assert set(reasons) == {"absent.paquet", "tests", "9mauvais"}
    assert all(reasons.values()) and "introuvable" in reasons["absent.paquet"]


def test_nothing_is_loaded_by_default():
    assert discover("") == ([], [])
    assert settings.EXTENSIONS == [] or settings.ORCH_EXTENSIONS  # aucune extension sans demande explicite


def test_the_menu_lists_only_what_the_role_allows(world, sample):
    admin_like = client_for(world["designer"])  # concepteur : « view » oui, « administer » non
    body = admin_like.get(BASE).json()
    assert body["api"] == EXTENSION_API
    assert [e["label"] for e in body["extensions"][0]["menu"]] == ["Exemple"]
    assert "refused" not in body
    assert body["extensions"][0]["ui"] == "/api/v1/workspaces/team-a/extensions/exemple/ui/index.js"


def test_an_extension_with_nothing_allowed_is_hidden(world, sample, settings):
    only_admin = copy.deepcopy(MANIFEST)
    only_admin["menu"] = [only_admin["menu"][1]]
    settings.EXTENSIONS = [Extension(parse_manifest(only_admin), "tests.sample_extension")]
    assert client_for(world["viewer"]).get(BASE).json()["extensions"] == []


def test_refusals_are_shown_to_administrators_only(world, sample, settings):
    from extensions.loader import Refusal
    from tenancy.models import Role
    from tests.conftest import make_user

    settings.EXTENSIONS_REFUSED = [Refusal("casse", "manifeste refusé")]
    assert "refused" not in client_for(world["viewer"]).get(BASE).json()
    admin = make_user("ada", world["ws_a"], Role.ADMIN)
    body = client_for(admin).get(BASE).json()
    assert body["refused"] == [{"name": "casse", "reason": "manifeste refusé"}]
    assert len(body["extensions"][0]["menu"]) == 2


def test_the_list_needs_a_member_of_the_space(world, sample):
    assert client_for(world["outsider"]).get(BASE).status_code == 404
    from rest_framework.test import APIClient

    assert APIClient().get(BASE).status_code in (401, 403)


def test_the_ui_file_is_served_and_confined(world, sample):
    c = client_for(world["viewer"])
    ok = c.get(BASE + "exemple/ui/index.js")
    assert ok.status_code == 200 and ok["Content-Type"].startswith("text/javascript")
    assert b"mount" in b"".join(ok.streaming_content)
    for path in (
        "exemple/ui/../secret.txt",
        "exemple/ui/%2e%2e/secret.txt",
        "exemple/ui/absent.js",
        "inconnue/ui/index.js",
    ):
        assert c.get(BASE + path).status_code == 404, path
    assert client_for(world["outsider"]).get(BASE + "exemple/ui/index.js").status_code == 404


def test_the_extension_routes_live_under_plugins_in_the_private_role(sample):
    routes = plugin_routes()
    assert [str(r.pattern) for r in routes] == ["plugins/exemple/"]
    from fluxonata.apiroles import role_of

    assert role_of(str(routes[0].pattern)) == "ui"


def test_the_ui_file_is_revalidated_not_cached_blindly(world, sample):
    c = client_for(world["viewer"])
    first = c.get(BASE + "exemple/ui/index.js")
    assert first["Cache-Control"] == "private, no-cache" and first["ETag"]
    again = c.get(BASE + "exemple/ui/index.js", HTTP_IF_NONE_MATCH=first["ETag"])
    assert again.status_code == 304 and not again.content
    assert c.get(BASE + "exemple/ui/index.js", HTTP_IF_NONE_MATCH='"autre"').status_code == 200
