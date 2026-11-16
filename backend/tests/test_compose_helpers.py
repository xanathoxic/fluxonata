"""Rendu Compose autonome des tests de topologie : configuration synthétique, jamais le vrai `.env.prod`, échec clair, omissions explicites."""

import hashlib
import os
import subprocess

import pytest

from tests import compose_helpers as ch

SENTINEL = "SENTINELLE-PROD-NE-DOIT-JAMAIS-APPARAITRE"
TEMPLATE = "ORCH_SECRET_KEY=\nORCH_DB_NAME=fluxonata\nORCH_DB_PASSWORD=\n# commentaire\nORCH_EXEC_AGENT_TOKEN=\n"
COMPOSE_OK = "services:\n  app:\n    image: alpine\n    env_file: .env.prod\n"


@pytest.fixture
def fake_infra(tmp_path):
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / ".env.prod.example").write_text(TEMPLATE, encoding="utf-8")
    (infra / ".env.prod").write_text(
        f"ORCH_SECRET_KEY={SENTINEL}\n", encoding="utf-8"
    )  # « vrai » fichier à ne jamais lire
    (infra / ".env.local").write_text(f"X={SENTINEL}\n", encoding="utf-8")
    (infra / "docker-compose.test.yml").write_text(COMPOSE_OK, encoding="utf-8")
    return infra


def test_synthetic_values_fill_only_the_empty_variables():
    text = ch.synthetic_env_text(TEMPLATE)
    assert (
        "ORCH_SECRET_KEY=synthetique-orch_secret_key" in text
        and "ORCH_DB_NAME=fluxonata" in text
        and "# commentaire" in text
    )
    assert "ORCH_EXEC_AGENT_TOKEN=synthetique-orch_exec_agent_token" in text


def test_the_tree_links_shipped_files_and_never_carries_the_real_env_files(fake_infra, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    tree = ch.build_tree(fake_infra, dest)
    assert (tree / "docker-compose.test.yml").is_symlink()  # le fichier réellement livré, pas une copie
    assert (tree / ".env.prod.example").is_symlink()
    env = tree / ".env.prod"
    assert env.is_file() and not env.is_symlink() and "synthetique" in env.read_text(encoding="utf-8")
    assert not (tree / ".env.local").exists()
    assert all(SENTINEL not in p.read_text(encoding="utf-8") for p in tree.iterdir() if p.is_file())


def test_diagnostics_never_show_secret_values():
    out = ch.redact("ORCH_SECRET_KEY=valeur-secrete\nPASSWORD: autre\nservice api: invalide\n")
    assert "valeur-secrete" not in out and "autre" not in out and "service api: invalide" in out


def _run_returning(monkeypatch, *results):
    calls = iter(results)
    monkeypatch.setattr(ch.subprocess, "run", lambda *a, **k: next(calls))


def _done(code, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_missing_docker_client_is_an_explicit_skip(monkeypatch, fake_infra):
    monkeypatch.setattr(ch.shutil, "which", lambda _: None)
    with pytest.raises(pytest.skip.Exception, match="client docker absent"):
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)


def test_missing_compose_plugin_is_an_explicit_skip_distinct_from_the_daemon(monkeypatch, fake_infra):
    monkeypatch.setattr(ch.shutil, "which", lambda _: "/usr/bin/docker")
    _run_returning(monkeypatch, _done(1, err="'compose' is not a docker command"))
    with pytest.raises(pytest.skip.Exception, match="plugin « docker compose » absent"):
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)


def test_an_invalid_configuration_fails_with_a_redacted_diagnostic(monkeypatch, fake_infra):
    monkeypatch.setattr(ch.shutil, "which", lambda _: "/usr/bin/docker")
    _run_returning(
        monkeypatch,
        _done(0, "Docker Compose version v5"),
        _done(1, err="services.app.bogus: invalide\nORCH_SECRET_KEY=abc123\n"),
    )
    with pytest.raises(pytest.fail.Exception) as exc:
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)
    assert "services.app.bogus: invalide" in str(exc.value) and "abc123" not in str(exc.value)


def test_unreadable_output_and_timeouts_fail_instead_of_skipping(monkeypatch, fake_infra):
    monkeypatch.setattr(ch.shutil, "which", lambda _: "/usr/bin/docker")
    _run_returning(monkeypatch, _done(0), _done(0, out="pas du json"))
    with pytest.raises(pytest.fail.Exception, match="illisible"):
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)

    def boom(cmd, **kw):
        if cmd[:3] == ["docker", "compose", "version"]:
            return _done(0)
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(ch.subprocess, "run", boom)
    with pytest.raises(pytest.fail.Exception, match="délai"):
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)


def test_real_compose_renders_the_fake_stack_without_the_real_env_file(fake_infra):
    """Exécution réelle (omise si le plugin Compose est absent) : le `env_file: .env.prod` de la pile factice est résolu vers le fichier synthétique."""
    conf = ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)
    env = conf["services"]["app"]["environment"]
    assert env["ORCH_SECRET_KEY"].startswith("synthetique") and SENTINEL not in str(conf)


def test_real_compose_fails_on_an_invalid_file(fake_infra):
    (fake_infra / "docker-compose.test.yml").write_text(
        "services:\n  app:\n    image: alpine\n    bogus_key: 1\n", encoding="utf-8"
    )
    with pytest.raises(pytest.fail.Exception, match="a échoué"):
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)


def _real_compose_stack(fake_infra):
    (fake_infra / "docker-compose.test.yml").write_text(
        "services:\n  app:\n    image: alpine\n    env_file: .env.prod\n    environment:\n"
        "      DEPUIS_PARENT: ${FLUX_SENTINELLE_PARENT:-non-defini}\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(
    os.geteuid() == 0, reason="exécuté en root : un fichier illisible ne prouve pas l'absence de lecture"
)
def test_an_existing_local_env_file_is_never_read_and_the_parent_environment_never_leaks(fake_infra, monkeypatch):
    """Faux `.env.prod` rendu ILLISIBLE (mode 000) : toute tentative de lecture échouerait. Sentinelles dans ce fichier et dans l'environnement du parent."""
    _real_compose_stack(fake_infra)
    env_file = fake_infra / ".env.prod"
    before = hashlib.sha256(env_file.read_bytes()).hexdigest(), env_file.stat().st_mtime_ns
    env_file.chmod(0)
    monkeypatch.setenv("FLUX_SENTINELLE_PARENT", SENTINEL)
    monkeypatch.setenv("ORCH_SECRET_KEY", SENTINEL)
    try:
        conf = ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)
    finally:
        env_file.chmod(0o600)
    env = conf["services"]["app"]["environment"]
    assert (
        env["ORCH_SECRET_KEY"] == "synthetique-orch_secret_key"
    )  # valeur synthétique du modèle, ni le fichier local ni le parent
    assert env["DEPUIS_PARENT"] == "non-defini"  # une variable du parent n'est pas interpolée
    assert SENTINEL not in str(conf)
    assert (
        hashlib.sha256(env_file.read_bytes()).hexdigest(),
        env_file.stat().st_mtime_ns,
    ) == before  # contenu et date inchangés


def test_diagnostics_do_not_carry_the_local_file_nor_the_parent_environment(fake_infra, monkeypatch):
    """Protection vérifiée : cette sentinelle ne sort pas. Le masquage reste un masquage par NOM de variable, pas une garantie universelle."""
    (fake_infra / "docker-compose.test.yml").write_text(
        "services:\n  app:\n    image: alpine\n    bogus_key: 1\n", encoding="utf-8"
    )
    monkeypatch.setenv("FLUX_SENTINELLE_PARENT", SENTINEL)
    monkeypatch.setenv("ORCH_SECRET_KEY", SENTINEL)
    with pytest.raises(pytest.fail.Exception) as exc:
        ch.compose_config(["docker-compose.test.yml"], infra=fake_infra)
    assert "a échoué" in str(exc.value) and SENTINEL not in str(exc.value)
