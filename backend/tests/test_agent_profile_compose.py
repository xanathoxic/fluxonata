"""Profil « par agents » : ce que la configuration Compose FUSIONNÉE (prod + rôles + agent) accorde réellement à chaque service.

Le rendu vient de `docker compose config` (valeurs synthétiques, aucun démon sollicité ; voir compose_helpers). Ces tests établissent la configuration livrée,
pas le comportement des conteneurs en marche (essayé séparément sur une pile jetable). Le profil agent ne rend vrai l'invariant « aucun service
ne détient à la fois le socket Docker et la clé du coffre » QUE pour les services actifs avec ce profil ; il ne protège pas la clé contre un attaquant qui
contrôle le service détenteur du socket (le socket équivaut à root sur l'hôte).
"""

import pytest

from tests.compose_helpers import compose_config

FILES = ("docker-compose.prod.yml", "docker-compose.roles.yml", "docker-compose.agent.yml")


@pytest.fixture(scope="module")
def merged():
    return compose_config(
        FILES,
        profiles=("agent",),
        extra_env={"ORCH_EGRESS_TEST_NETWORK": "reseau-de-test-synthetique"},
    )


def active(services):
    return {n: s for n, s in services.items() if not s.get("profiles") or "agent" in s["profiles"]}


def has_socket(s):
    return any("docker.sock" in v.get("source", "") for v in s.get("volumes", []))


def has_keys(s):
    return any(v.get("target") == "/keys" for v in s.get("volumes", []))


def test_no_active_service_holds_both_the_docker_socket_and_the_vault_key(merged):
    services = active(merged["services"])
    assert [n for n, s in services.items() if has_socket(s) and has_keys(s)] == []
    assert [n for n, s in services.items() if has_socket(s)] == ["exec-agent"]  # un seul détenteur du socket


def test_the_socket_holder_has_neither_database_variables_nor_keys_nor_artifacts_and_one_network(merged):
    agent = merged["services"]["exec-agent"]
    env = agent.get("environment") or {}
    assert not [k for k in env if k.startswith("ORCH_DB_")] and "ORCH_SECRET_KEY" not in env
    assert not has_keys(agent) and not any("artifacts" in v.get("target", "") for v in agent.get("volumes", []))
    assert sorted(agent["networks"]) == ["edge"] and "ORCH_RUNNER_TOKEN" in env


def test_the_agent_uses_the_same_test_service_network_as_the_stack(merged):
    # défaut trouvé à l'essai : sans cette variable l'agent visait le réseau du développement (« fluxonata-egress-test »)
    assert merged["services"]["exec-agent"]["environment"]["ORCH_EGRESS_TEST_NETWORK"] == "reseau-de-test-synthetique"
    assert merged["networks"]["egress_test"]["name"] == "reseau-de-test-synthetique"


def test_the_socket_holding_services_of_the_other_topologies_are_off_in_this_profile(merged):
    # absents du rendu : leurs profils ne sont pas activés ; ils cumulent socket et clé (donc pas d'essai à blanc de l'assistant dans ce profil)
    assert "worker-exec" not in merged["services"] and "worker-ai" not in merged["services"]


def test_only_the_agent_receives_the_agent_token(merged):
    """Le jeton (valeur synthétique de `.env.agent`) n'est dans l'environnement d'AUCUN autre service, ni sous son nom ni sous celui d'avant."""
    services = merged["services"]
    token = services["exec-agent"]["environment"]["ORCH_RUNNER_TOKEN"]
    assert token.startswith("synthetique-")
    for name, s in services.items():
        env = s.get("environment") or {}
        if name != "exec-agent":
            assert "ORCH_RUNNER_TOKEN" not in env and "ORCH_EXEC_AGENT_TOKEN" not in env, name
            assert token not in env.values(), name


def test_the_agent_environment_holds_the_token_and_no_other_secret_of_the_stack(merged):
    env = merged["services"]["exec-agent"]["environment"]
    assert "ORCH_EXEC_AGENT_TOKEN" not in env and "ORCH_DB_PASSWORD" not in env and "ORCH_SECRET_KEY" not in env


def test_worker_aux_takes_the_ai_steps_without_getting_the_socket(merged):
    aux = merged["services"]["worker-aux"]
    assert aux["environment"]["ORCH_AUX_RUNS_AI_STEPS"] == "1" and not has_socket(aux)
    assert aux["environment"]["ORCH_ROLES"] == "aux"  # le rôle exec (donc le socket) ne lui est pas donné
