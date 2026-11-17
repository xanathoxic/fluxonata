"""Topologie « agents distants » : ce que la configuration LIVRÉE accorde au plan de contrôle, au front des agents et à l'agent autonome.

Ces tests établissent la configuration livrée (rendu `docker compose config`, lecture de `nginx.runner-edge.conf`), pas le comportement des
conteneurs en marche, qui n'a PAS été éprouvé sur un démon distinct (preuve à produire). Invariant visé, côté plan de contrôle : aucun service n'y détient
de socket d'exécution, donc rien n'y exécute d'action (et rien n'y est exposé à un démon Docker compromis).
"""

import re
from pathlib import Path

import pytest
import yaml

from tests.compose_helpers import compose_config

INFRA = Path(__file__).resolve().parents[2] / "infra"
CONTROL_PLANE = ("docker-compose.prod.yml", "docker-compose.roles.yml", "docker-compose.remote-agents.yml")
DIGEST = "@sha256:" + "a" * 64


@pytest.fixture(scope="module")
def plane():
    return compose_config(CONTROL_PLANE, extra_env={"ORCH_RUNNER_TLS_DIR": "/etc/fluxonata/tls"})


@pytest.fixture(scope="module")
def kit():
    return compose_config(
        ("agent/docker-compose.yml",),
        extra_env={
            "ORCH_URL": "https://runners.exemple.test:8443",
            "ORCH_PYTHON_IMAGE": "python" + DIGEST,
            "ORCH_NODE_IMAGE": "node" + DIGEST,
            "ORCH_POWERSHELL_IMAGE": "powershell" + DIGEST,
        },
    )


def has_socket(s):
    return any("docker.sock" in v.get("source", "") for v in s.get("volumes", []))


def targets(s):
    return {v["target"] for v in s.get("volumes", [])}


# --- plan de contrôle -----------------------------------------------------------------------------------------------------------------------
def test_I8_no_service_of_the_control_plane_holds_an_execution_socket(plane):
    assert [n for n, s in plane["services"].items() if has_socket(s)] == []
    assert not [n for n in plane["services"] if n.startswith(("exec-agent", "worker-exec", "worker-ai"))]


def test_the_control_plane_still_runs_what_it_needs_without_the_execution_roles(plane):
    assert {"api-ui", "api-ingest", "runner-gw", "orchestrator", "worker-aux", "db", "pgbouncer", "runner-edge"} <= set(
        plane["services"]
    )
    assert plane["services"]["worker-aux"]["environment"]["ORCH_AUX_RUNS_AI_STEPS"] == "1"


def test_runner_edge_sees_only_the_gateway_and_publishes_one_tls_port(plane):
    edge = plane["services"]["runner-edge"]
    assert sorted(edge["networks"]) == ["gw", "runner_front"]  # ni edge (api-ui, api-ingest, web), ni data (PostgreSQL)
    assert [p["target"] for p in edge["ports"]] == [8443]
    assert targets(edge) == {"/etc/nginx/tls"} and edge["volumes"][0]["read_only"] is True
    assert (
        edge["read_only"] is True and edge["cap_drop"] == ["ALL"] and "no-new-privileges:true" in edge["security_opt"]
    )
    assert not edge.get("env_file") and not edge.get("environment")  # aucun secret, aucune variable du plan de contrôle


def test_the_gateway_network_is_internal_and_shared_by_two_services_only(plane):
    members = [n for n, s in plane["services"].items() if "gw" in s.get("networks", {})]
    assert sorted(members) == ["runner-edge", "runner-gw"]
    assert plane["networks"]["gw"]["internal"] is True


def test_only_web_and_runner_edge_publish_a_port(plane):
    assert sorted(n for n, s in plane["services"].items() if s.get("ports")) == ["runner-edge", "web"]


# --- front des agents -----------------------------------------------------------------------------------------------------------------------
CONF = (INFRA / "nginx.runner-edge.conf").read_text(encoding="utf-8")


def test_the_edge_serves_only_the_runner_protocol_and_everything_else_is_a_404():
    assert re.findall(r"location (=? ?\S+) \{", CONF) == ["= /healthz", "/api/v1/runner/", "/"]
    assert re.search(r"location / \{\s*return 404;", CONF)
    assert re.findall(r"proxy_pass (\S+);", CONF) == ["$UP"] and re.findall(r"set \$UP (\S+);", CONF) == [
        "http://runner-gw:8000"
    ]
    from fluxonata.apiroles import RUNNER_PREFIXES

    assert tuple(p.removeprefix("/api/v1/").rstrip("/") for p in ["/api/v1/runner/"]) == tuple(
        p.rstrip("/") for p in RUNNER_PREFIXES
    )


def test_the_edge_is_tls_only_and_bounded():
    assert re.findall(r"listen (\S+)", CONF) == ["8443"] and "listen 80" not in CONF
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in CONF and "ssl_certificate " in CONF and "ssl_certificate_key" in CONF
    assert (
        re.search(r"client_max_body_size \d+m;", CONF)
        and "limit_req zone=" in CONF
        and "limit_conn runner_conn" in CONF
    )
    assert "if ($request_method !~ ^(GET|POST)$)" in CONF


def test_the_edge_never_logs_a_token_a_query_string_or_a_body():
    fmt = re.search(r"log_format runner (.*?);", CONF, re.S).group(1)
    for forbidden in (
        "http_authorization",
        "request_body",
        "$args",
        "query_string",
        "$request ",
        "request_uri",
        '$request"',
    ):
        assert forbidden not in fmt, forbidden
    assert "access_log /dev/stdout runner;" in CONF and "error_log /dev/stderr crit;" in CONF


# --- agent autonome -------------------------------------------------------------------------------------------------------------------------
ALLOWED_ENV = {
    "ORCH_URL",
    "ORCH_RUNNER_TOKEN_FILE",
    "ORCH_RUNNER_CONCURRENCY",
    "ORCH_CA_FILE",
    "ORCH_HARDENED",
    "ORCH_REQUIRE_IMAGE_DIGEST",
    "ORCH_WORK_DIR",
    "ORCH_PYTHON_IMAGE",
    "ORCH_NODE_IMAGE",
    "ORCH_POWERSHELL_IMAGE",
    "DOCKER_HOST",
}


def test_the_standalone_agent_receives_only_what_an_agent_needs(kit):
    agent = kit["services"]["agent"]
    assert set(agent["environment"]) <= ALLOWED_ENV
    assert not agent.get("env_file")
    assert "ORCH_RUNNER_TOKEN" not in agent["environment"] and agent["environment"][
        "ORCH_RUNNER_TOKEN_FILE"
    ].startswith("/run/secrets/")  # le jeton vient d'un fichier, jamais de l'environnement ni d'une ligne de commande
    assert [s["source"] for s in agent["secrets"]] == ["runner_token"] and not agent.get("command")
    work = agent["environment"]["ORCH_WORK_DIR"]
    assert targets(agent) == {
        "/var/run/docker.sock",
        work,
    }  # rien d'autre : ni volume de la plate-forme, ni clés, ni artefacts
    assert agent["read_only"] is True and agent["cap_drop"] == ["ALL"] and agent["user"] != "0:0"


def test_the_standalone_agent_requires_pinned_images_and_enforces_it():
    text = (INFRA / "agent" / "docker-compose.yml").read_text(encoding="utf-8")
    services = yaml.safe_load(text)["services"]["agent"]["environment"]
    for var in ("ORCH_PYTHON_IMAGE", "ORCH_NODE_IMAGE", "ORCH_POWERSHELL_IMAGE"):
        assert ":?" in services[var], f"{var} doit être exigée (sans valeur par défaut flottante)"
    assert services["ORCH_REQUIRE_IMAGE_DIGEST"].endswith(":-1}")  # exigé par défaut


def test_nothing_of_the_control_plane_is_referenced_by_the_agent_kit():
    for name in ("docker-compose.yml", "agent.env.example", "fluxonata-agent.service", "agent-ctl.sh"):
        lines = (INFRA / "agent" / name).read_text(encoding="utf-8").splitlines()
        text = "\n".join(
            re.sub(r"\s+#.*$", "", ln) for ln in lines if not ln.lstrip().startswith("#")
        )  # les commentaires peuvent NOMMER ce qui est exclu
        assert (
            ".env.prod" not in text and "ORCH_DB_" not in text and "ORCH_SECRET_KEY" not in text and "keys:" not in text
        ), name
    example = (INFRA / "agent" / "agent.env.example").read_text(encoding="utf-8")
    assert not re.search(
        r"^[A-Z_]*(TOKEN|SECRET|PASSWORD|KEY)[A-Z_]*=\S", example, re.M
    )  # aucun secret dans le fichier de configuration


def test_the_systemd_unit_reads_the_token_as_a_credential_and_is_confined():
    unit = (INFRA / "agent" / "fluxonata-agent.service").read_text(encoding="utf-8")
    assert "LoadCredential=runner_token:" in unit and "ORCH_RUNNER_TOKEN_FILE=%d/runner_token" in unit
    assert "orr_" not in unit and "ORCH_RUNNER_TOKEN=" not in unit and "--token" not in unit
    assert "NoNewPrivileges=true" in unit and "ProtectSystem=strict" in unit and "RestartPreventExitStatus=2 4" in unit


# --- le mode « même démon » n'est pas présenté comme une isolation --------------------------------------------------------------------------
def test_the_shared_daemon_profile_is_labelled_and_needs_an_explicit_acceptance():
    text = (INFRA / "docker-compose.agent.yml").read_text(encoding="utf-8")
    agent = yaml.safe_load(text)["services"]["exec-agent"]
    assert agent["labels"]["fluxonata.isolation"] == "shared-docker-daemon"
    assert ":?" in agent["environment"]["ORCH_ACCEPT_SHARED_DOCKER_AGENT"]  # sans l'option, Compose refuse de démarrer
    assert "N'EST PAS UNE ISOLATION" in text and "I8" in text
