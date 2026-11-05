"""AR2.4 + AR2.7 — la topologie PAR RÔLES (infra/docker-compose.roles.yml) : privilèges, réseaux, routage, profils.

On interroge `docker compose config --format json`, qui donne la configuration FUSIONNÉE exacte (pas une réinterprétation maison des règles de fusion).
Sauté si Docker Compose est absent. Pas de conteneur démarré.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"


def merged(*profiles):
    if not shutil.which("docker"):
        pytest.skip("docker indisponible")
    env = {**os.environ, "ORCH_DB_PASSWORD": "x", "ORCH_WORK_DIR": "/srv/work"}
    cmd = [
        "docker",
        "compose",
        "-f",
        str(INFRA / "docker-compose.prod.yml"),
        "-f",
        str(INFRA / "docker-compose.roles.yml"),
        "--env-file",
        str(INFRA / ".env.prod.example"),
    ]
    for p in profiles:
        cmd += ["--profile", p]
    out = subprocess.run([*cmd, "config", "--format", "json"], capture_output=True, text=True, env=env, timeout=60)
    if out.returncode:
        pytest.skip(f"docker compose config indisponible : {out.stderr[:150]}")
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def stack():
    return merged()


@pytest.fixture(scope="module")
def stack_ai():
    return merged("ai")


def targets(s):
    return sorted(v["target"] for v in s.get("volumes", []))


def nets(s):
    return sorted(s.get("networks", {}))


def has_socket(s):
    return any("docker.sock" in v["target"] for v in s.get("volumes", []))


def test_the_compact_services_are_replaced_not_duplicated(stack):
    assert "api" not in stack["services"] and "worker" not in stack["services"]
    assert {
        "api-ui",
        "api-ingest",
        "runner-gw",
        "orchestrator",
        "worker-exec",
        "worker-aux",
        "web",
        "db",
        "pgbouncer",
        "migrate",
    } <= set(stack["services"])


def test_the_ai_worker_only_starts_on_request(stack, stack_ai):
    assert "worker-ai" not in stack["services"] and "worker-ai" in stack_ai["services"]


@pytest.mark.parametrize(
    "name, role",
    [
        ("api-ui", "ui"),
        ("api-ingest", "ingest"),
        ("runner-gw", "runner-gw"),
        ("worker-exec", "exec"),
        ("worker-aux", "aux"),
    ],
)
def test_each_service_carries_exactly_its_role(stack, name, role):
    assert stack["services"][name]["environment"]["ORCH_ROLES"] == role


def test_ingest_has_no_way_out_and_no_artifacts(stack):
    """ARCHITECTURE.md I3 : le rôle exposé à Internet n'a aucune sortie réseau AU NIVEAU DU CONTENEUR, ni accès aux artefacts, ni socket."""
    s = stack["services"]["api-ingest"]
    assert nets(s) == ["data", "edge"] and not has_socket(s)
    assert stack["networks"]["data"]["internal"] and stack["networks"]["edge"]["internal"]
    assert targets(s) == ["/keys"]  # la clé, pour vérifier la signature d'un webhook ; rien d'autre
    assert "egress" not in nets(s) and "front" not in nets(s)


def test_only_the_services_that_need_the_outside_are_on_the_egress_network(stack):
    outside = sorted(n for n, s in stack["services"].items() if "egress" in nets(s))
    assert outside == ["api-ui", "runner-gw", "worker-aux", "worker-exec"]  # pas l'orchestrateur (AR4.2)
    assert not stack["networks"]["egress"].get("internal")
    for n in ("db", "pgbouncer", "migrate"):
        assert nets(stack["services"][n]) == ["data"]  # la base ne parle qu'au réseau interne


def test_only_web_is_on_the_front_network_and_publishes_a_port(stack):
    assert [n for n, s in stack["services"].items() if "front" in nets(s)] == ["web"]
    assert [n for n, s in stack["services"].items() if s.get("ports")] == ["web"]


def test_docker_socket_holders(stack, stack_ai):
    assert [n for n, s in stack["services"].items() if has_socket(s)] == ["worker-exec"]
    assert sorted(n for n, s in stack_ai["services"].items() if has_socket(s)) == [
        "worker-ai",
        "worker-exec",
    ]  # l'essai à blanc de l'assistant (C-2)


def test_the_light_roles_use_the_runtime_image_without_docker_cli_and_are_locked_down(stack):
    for n in ("api-ui", "api-ingest", "runner-gw", "worker-aux"):
        s = stack["services"][n]
        assert s["image"] == "fluxonata-backend:local" and "user" not in s
        assert s["cap_drop"] == ["ALL"] and s["read_only"] and "no-new-privileges:true" in s["security_opt"]
        assert s["environment"]["ORCH_KEY_AUTOCREATE"] == "0" and s["environment"]["ORCH_DB_HOST"] == "pgbouncer"
    for n in ("worker-exec",):
        assert stack["services"][n]["image"] == "fluxonata-worker:local" and stack["services"][n]["user"] == "0:0"


def test_artifacts_are_given_only_to_roles_that_read_or_write_them(stack):
    holders = sorted(n for n, s in stack["services"].items() if "/app/var/artifacts" in targets(s))
    assert holders == [
        "api-ui",
        "runner-gw",
        "worker-aux",
        "worker-exec",
    ]  # pas l'orchestrateur : la rétention tourne dans worker-aux


def test_every_role_has_a_health_probe_and_a_stop_delay(stack):
    for n in ("api-ui", "api-ingest", "runner-gw", "worker-exec", "worker-aux", "orchestrator"):
        s = stack["services"][n]
        assert s["healthcheck"]["test"] and s["stop_grace_period"]


def test_long_polls_get_threads_and_slots_proportionate_to_each_other(stack):
    gw = stack["services"]["runner-gw"]
    threads = int(gw["command"][gw["command"].index("--threads") + 1])
    assert (
        int(gw["environment"]["ORCH_RUNNER_LONG_POLL_SLOTS"]) <= threads // 2
    )  # jamais plus de la moitié des threads d'un process (AR1.3)


def test_web_uses_the_role_aware_nginx_and_does_not_wait_for_a_healthy_role(stack):
    web = stack["services"]["web"]
    assert (
        web["image"] == "fluxonata-web-roles:local" and web["build"]["args"]["NGINX_CONF"] == "infra/nginx.roles.conf"
    )
    assert all(
        v["condition"] == "service_started" for v in web["depends_on"].values()
    )  # un rôle malade n'empêche pas de servir les autres


# --- nginx par rôles ----------------------------------------------------------------------------------------------------------
CONF = (INFRA / "nginx.roles.conf").read_text(encoding="utf-8")
LOCATIONS = dict(re.findall(r"location (\S+) \{\s*set \$UP (\S+);", CONF))


def test_nginx_routes_each_surface_to_its_own_service_and_starts_without_them():
    assert LOCATIONS == {
        "/api/v1/hooks/": "http://api-ingest:8000",
        "/api/v1/forms/": "http://api-ingest:8000",
        "/api/v1/resume-forms/": "http://api-ingest:8000",
        "/api/v1/runner/": "http://runner-gw:8000",
        "/api/": "http://api-ui:8000",
    }
    assert (
        "resolver 127.0.0.11" in CONF
    )  # noms résolus à la demande : nginx démarre même si un rôle est arrêté (502 sur cette surface seulement)
    assert not re.search(r"proxy_pass http://[a-z-]+:\d+", CONF), (
        "un proxy_pass statique empêcherait nginx de démarrer sans ce service"
    )


def test_nginx_prefixes_match_the_application_roles():
    """Le proxy et l'application doivent s'accorder sur qui sert quoi (fluxonata/apiroles.py)."""
    from fluxonata.apiroles import INGEST_PREFIXES, RUNNER_PREFIXES

    ingest = {p.removeprefix("/api/v1/") for p, up in LOCATIONS.items() if "api-ingest" in up}
    runner = {p.removeprefix("/api/v1/") for p, up in LOCATIONS.items() if "runner-gw" in up}
    assert ingest == set(INGEST_PREFIXES) and runner == set(RUNNER_PREFIXES)


def test_the_orchestrator_is_database_only(stack):
    o = stack["services"]["orchestrator"]
    assert (
        nets(o) == ["data"] and not o.get("volumes") and not has_socket(o)
    )  # I4 : ni clé, ni artefacts, ni sortie réseau
