"""Étape SSH : validation de la connexion, de l'hôte choisi par l'étape, de la chaîne de rebonds, des commandes et des étapes de session."""

import pytest

from runs import ssh_node, ssh_shell
from runs.ssh_core import SshStepError

FP = "SHA256:" + "A" * 43


def _res(v, ctx):
    return v


def refused(fn, *a, match=None, **k):
    with pytest.raises(SshStepError, match=match) as exc:
        fn(*a, **k)
    return exc.value


# ---- réglages d'une connexion


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({"port": "99999"}, "Port invalide"),
        ({"port": "abc"}, "Port invalide"),
        ({"host": "http://x"}, "Nom d'hôte"),
        ({"host": "srv.test"}, "empreinte"),
        ({"host": "srv.test", "host_key_sha256": "SHA256:court"}, "empreinte"),
        ({"host_key_sha256": FP}, "identité réutilisable"),
        ({"connect_timeout_seconds": "0"}, "de 1 à 60"),
        ({"auth_timeout_seconds": "61"}, "de 1 à 60"),
        ({"passphrase_secret": "mauvais nom !"}, "passphrase_secret"),
    ],
)
def test_connection_settings_refusals(config, match):
    refused(ssh_node.check_connection_config, config, match=match)


def test_connection_settings_accept_a_pinned_host_or_a_bare_identity():
    ssh_node.check_connection_config(
        {"host": "srv.test", "port": "22", "host_key_sha256": FP, "passphrase_secret": "cle-1"}
    )
    ssh_node.check_connection_config({"username": "robot"})


# ---- hôte choisi par l'étape


def endpoint(item, found_host="", name="c"):
    return ssh_node._step_endpoint(item, found_host, name, _res, {})


def test_a_step_endpoint_is_none_when_the_connection_brings_its_host():
    assert endpoint({}, found_host="srv.test") is None
    refused(endpoint, {"port": 2222}, found_host="srv.test", match="définit son hôte")
    refused(endpoint, {"host_key_sha256": FP}, found_host="srv.test", match="définit son hôte")


def test_a_step_endpoint_needs_a_valid_host_port_and_pinned_key():
    refused(endpoint, {}, match="n'a pas d'hôte")
    refused(endpoint, {"host": "autre.test"}, found_host="srv.test", match="définit déjà son hôte")
    refused(endpoint, {"host": "http://x", "host_key_sha256": FP}, match="Hôte de l'étape invalide")
    for port in (0, 70000, "x1", True, "١٢"):
        refused(endpoint, {"host": "h.test", "port": port, "host_key_sha256": FP}, match="Port de l'étape")
    refused(endpoint, {"host": "h.test"}, match="empreinte")
    refused(endpoint, {"host": "h.test", "host_key_sha256": "SHA256:court"}, match="empreinte")
    assert endpoint({"host": "H.test", "host_key_sha256": FP}) == {"host": "h.test", "port": 22, "fingerprints": [FP]}
    assert endpoint({"host": "h.test", "port": "2222", "host_key_sha256": FP})["port"] == 2222


# ---- chaîne de rebonds


def lookup(name):
    return {"a": ("a", "h1.test", "22"), "b": ("b", "h2.test", "22")}.get(name)


def test_the_chain_is_bounded_named_and_without_loops():
    refused(ssh_node._chain, {"connection": "a", "jumps": ["b"] * 9}, lookup, _res, {}, match="Trop de rebonds")
    refused(ssh_node._chain, {"connection": "{{ x }}"}, lookup, _res, {}, match="noms fixes")
    refused(ssh_node._chain, {"connection": "absente"}, lookup, _res, {}, match="introuvable|CONNECTION")
    refused(ssh_node._chain, {"connection": "a", "jumps": ["a"]}, lookup, _res, {}, match="même serveur")
    chain, endpoints = ssh_node._chain({"connection": "a", "jumps": ["b"]}, lookup, _res, {})
    assert [c["name"] for c in chain] == ["b", "a"] and endpoints == [None, None]


# ---- commandes et session


def prepare(**node):
    return ssh_node.prepare({"connection": "a", **node}, _res, {}, 1, None, lookup=lookup)


def test_session_kind_and_commands_are_checked():
    refused(prepare, session="telnet", match="exec")
    refused(prepare, commands="pas-une-liste-valide" and {"a": 1}, match="liste")
    refused(prepare, commands=[], match="au moins une commande")
    refused(prepare, commands=[5], match="un texte ou un objet")
    refused(prepare, commands=[{"id": "x", "run": "ls"}, {"id": "x", "run": "ls"}], match="identifiant")
    refused(prepare, commands=[{"run": "ls", "expect_exit": [999]}], match="expect_exit")
    spec = prepare(commands="uptime")
    assert spec["commands"][0]["run"] == "uptime" and spec["jumps"] == [] and spec["mode"] == "test"


# ---- étapes de session interactive


def steps(raw, aliases=None):
    return ssh_shell.prepare_steps(raw, _res, {}, aliases or {})


def test_session_steps_refusals():
    refused(steps, [], match="au moins une étape")
    refused(steps, ["texte"], match="un objet")
    refused(steps, [{"id": "s1", "send": "x"}, {"id": "s1", "send": "y"}], match="identifiant")
    refused(steps, [{"id": "s1"}], match="send")
    refused(steps, [{"id": "s1", "send": "x", "responses": ["texte"], "expect": "ok"}], match="un objet")
    refused(steps, [{"id": "s1", "send": "x", "responses": [{"expect": "mdp"}], "expect": "ok"}], match="send")
    refused(steps, [{"id": "s1", "send": "x", "responses": [{"expect": "a", "send": "b"}]}], match="expect")


def test_a_success_pattern_is_also_what_the_step_waits_for():
    out = steps([{"id": "s1", "send": "ls", "success": "ok"}])
    assert out[0]["expect"] == out[0]["success"] and out[0]["expect"]


def test_pty_settings_must_be_an_object():
    refused(ssh_shell.clean_pty, "oui", match="PTY")


def test_pty_bounds_and_defaults():
    assert ssh_shell.clean_pty(None) == {"enabled": False}
    assert ssh_shell.clean_pty(True) == {"enabled": True, "term": "xterm", "cols": 80, "rows": 24}
    refused(ssh_shell.clean_pty, {"term": "mauvais terminal !"}, match="terminal")
    refused(ssh_shell.clean_pty, {"cols": 10}, match="colonnes")
    refused(ssh_shell.clean_pty, {"rows": True}, match="lignes")
