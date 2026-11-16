"""Outils en ligne de commande (scripts et exemples) : l'adresse de la plate-forme ne peut viser que http(s), et le banc de mise à jour
n'envoie la signature d'un webhook que vers la pile locale. Aucun appel réseau."""

import argparse
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tools():
    return {
        "sync": load("scripts/workflows_sync.py", "wf_sync"),
        "install": load("examples/tests-automatisation/outils/installer_ressources.py", "installer"),
        "webhook": load("examples/tests-automatisation/outils/envoyer_webhook.py", "envoyer"),
    }


ACCEPTED = [
    "http://127.0.0.1:8100",
    "http://127.0.0.1:5173/",
    "https://flux.exemple.org",
    "https://flux.exemple.org:8443/base",
]
REFUSED = [
    "file:///etc/passwd",
    "ftp://flux.exemple.org",
    "javascript:alert(1)",
    "127.0.0.1:8100",
    "",
    "http://",
    "https:///chemin",
    "data:text/plain,x",
]


@pytest.mark.parametrize("url", ACCEPTED)
def test_accepted_addresses_are_returned_unchanged(tools, url):
    assert tools["sync"].adresse_http(url) == url
    assert tools["install"].adresse_http(url) == url
    assert tools["webhook"].adresse_http(url) == url


@pytest.mark.parametrize("url", REFUSED)
def test_other_schemes_and_incomplete_addresses_are_refused(tools, url):
    with pytest.raises(argparse.ArgumentTypeError):
        tools["sync"].adresse_http(url)
    with pytest.raises(argparse.ArgumentTypeError):
        tools["install"].adresse_http(url)
    with pytest.raises(ValueError):
        tools["webhook"].adresse_http(url)


def test_webhook_tool_refuses_a_file_url_before_any_request(tools, monkeypatch, capsys):
    monkeypatch.setenv("FLUXONATA_WEBHOOK_SECRET", "secret-de-test")
    called = []
    monkeypatch.setattr(tools["webhook"].urllib.request, "urlopen", lambda *a, **k: called.append(a))
    assert tools["webhook"].main(["envoyer_webhook.py", "file:///etc/passwd", "{}"]) == 2
    assert not called
    assert "refusée" in capsys.readouterr().err


def test_sync_tool_parser_rejects_a_bad_url(tools, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["workflows_sync.py", "export", "--url", "file:///etc/passwd", "--workspace", "demo", "--out", "x"]
    )
    with pytest.raises(SystemExit) as exc:
        tools["sync"].main()
    assert exc.value.code == 2


@pytest.fixture(scope="module")
def upgrade():
    return load("scripts/rolling_upgrade_test.py", "rolling_upgrade")


def test_local_path_accepts_relative_and_local_addresses(upgrade):
    port = upgrade.PORT
    assert upgrade.local_path("/api/v1/hooks/abc/") == "/api/v1/hooks/abc/"
    assert upgrade.local_path(f"http://127.0.0.1:{port}/api/v1/hooks/abc/?x=1") == "/api/v1/hooks/abc/?x=1"
    assert upgrade.local_path(f"http://localhost:{port}/h/") == "/h/"


@pytest.mark.parametrize(
    "url",
    [
        "https://exemple.org/h/",
        "http://exemple.org/h/",
        "http://127.0.0.1:1/h/",
        "file:///etc/passwd",
        "//exemple.org/h/",
    ],
)
def test_local_path_refuses_other_hosts_ports_and_schemes(upgrade, url):
    with pytest.raises(ValueError):
        upgrade.local_path(url)
