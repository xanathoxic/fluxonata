"""AR3.7 (version d'agent) et AR3.8 (empreinte de l'image d'exécution)."""

import threading

import pytest

from runs import engine
from tests.test_runners import R, agent, ask, boss, new_runner  # noqa: F401 (fixtures et aides)


# --- AR3.7 : numéro de protocole et version minimale d'agent ---------------------------------------------------------------------
def call(c, ua=None):
    kw = {"HTTP_USER_AGENT": ua} if ua else {}
    return c.post(f"{R}/jobs/request/", {"wait": 0}, format="json", **kw)


def test_the_platform_announces_its_protocol_and_the_minimum_agent(world, boss, settings):  # noqa: F811
    settings.ORCH_MIN_AGENT_VERSION = "0.2.0"
    me = agent(new_runner(boss)["token"]).get(f"{R}/me/").json()
    assert me["protocol"] == 1 and me["min_agent_version"] == "0.2.0"


def test_without_a_requirement_every_agent_is_accepted(world, boss, settings):  # noqa: F811
    settings.ORCH_MIN_AGENT_VERSION = ""
    c = agent(new_runner(boss)["token"])
    assert call(c, "fluxonata-runner/0.0.1").status_code == 204 and call(c).status_code == 204


@pytest.mark.parametrize(
    "ua, accepted",
    [
        ("fluxonata-runner/0.1.0", False),
        ("fluxonata-runner/0.2.0", True),
        ("fluxonata-runner/0.10.0", True),
        ("fluxonata-runner/1.0.0 extra", True),
        (None, False),
        ("curl/8", False),
    ],
)
def test_an_agent_older_than_the_minimum_gets_426_with_the_version_to_install(world, boss, settings, ua, accepted):  # noqa: F811
    settings.ORCH_MIN_AGENT_VERSION = "0.2.0"
    r = call(agent(new_runner(boss)["token"]), ua)
    if accepted:
        assert r.status_code == 204
    else:
        assert r.status_code == 426 and r.json()["error"]["code"] == "agent_too_old"
        assert r.json()["error"]["details"] == {"min_agent_version": "0.2.0"}


def test_the_agent_stops_with_its_own_exit_code_when_it_is_too_old(monkeypatch, caplog):
    import logging
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
    import agent as agent_mod

    class Client:
        def call(self, method, path, body=None, timeout=30.0):
            return 426, {"error": {"code": "agent_too_old", "details": {"min_agent_version": "0.2.0"}}}

    a = agent_mod.Agent.__new__(agent_mod.Agent)
    a.client, a.config, a.once, a.idle_exit, a.stop = Client(), {"long_poll_seconds": 20}, False, 0.0, threading.Event()
    with caplog.at_level(logging.ERROR):
        a._slot()
    assert a.too_old and a.stop.is_set() and "0.2.0" in caplog.text and not a.revoked


# --- AR3.8 : empreinte de l'image d'exécution ---------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_cache():
    engine._digest_cache.clear()
    yield
    engine._digest_cache.clear()


def test_a_pinned_image_is_its_own_digest_and_no_docker_is_called(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("aucun appel à Docker pour une image épinglée"))
    assert engine._image_digest("python@sha256:" + "a" * 64) == "sha256:" + "a" * 64


def test_without_a_docker_cli_an_unpinned_image_has_no_digest_and_nothing_is_run(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("pas de CLI : pas d'appel"))
    assert engine._image_digest("python:3.13-slim") is None


def test_with_a_docker_cli_the_local_daemon_is_asked_once(monkeypatch):
    calls = []

    class Out:
        stdout = "sha256:deadbeef\n"

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a) or Out())
    assert engine._image_digest("python:3.13-slim") == "sha256:deadbeef" == engine._image_digest("python:3.13-slim")
    assert len(calls) == 1


def test_the_run_definition_records_the_pinned_digest(world, settings):
    settings.ORCH_PYTHON_IMAGE = "python@sha256:" + "b" * 64
    assert engine._runtime_info() == {"image": settings.ORCH_PYTHON_IMAGE, "digest": "sha256:" + "b" * 64}


def test_health_warns_about_an_unpinned_runtime_image_outside_development(world, settings):
    from core import health

    settings.DEBUG = False
    settings.ORCH_PYTHON_IMAGE = "python:3.13-slim"
    got = {i["key"]: i for i in health.summary(world["ws_a"])}
    assert got["image_pin"]["status"] == "warn" and "pin-images.sh" in got["image_pin"]["hint"]
    settings.ORCH_PYTHON_IMAGE = "python@sha256:" + "c" * 64
    assert "image_pin" not in {i["key"] for i in health.summary(world["ws_a"])}
    settings.ORCH_PYTHON_IMAGE, settings.DEBUG = "python:3.13-slim", True
    assert "image_pin" not in {i["key"] for i in health.summary(world["ws_a"])}  # développement : pas de bruit
