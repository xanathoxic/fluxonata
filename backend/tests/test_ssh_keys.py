"""Chargement des clés privées (nœud SSH et nœud SFTP) avec la version de paramiko installée : clés synthétiques, aucun serveur réel.

Défaut démontré avant correction : `paramiko.PKey.from_private_key` (utilisé par le nœud SFTP) échoue pour TOUT format avec cette version
(« PKey.__init__() got an unexpected keyword argument 'file_obj' ») : l'authentification SFTP par clé ne marchait pour aucune clé."""

import io

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from runs import sftp_node
from runs.ssh_core import load_private_key
from tests.ssh_fake import FakeSshd
from tests.ssh_helpers import make_conn, make_secret

PASSPHRASE = "phrase-synthetique-4321"
OPENSSH, PKCS1, PKCS8 = (
    serialization.PrivateFormat.OpenSSH,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.PrivateFormat.PKCS8,
)


def make(kind, fmt, passphrase=None):
    priv = {
        "ed25519": lambda: ed25519.Ed25519PrivateKey.generate(),
        "ecdsa": lambda: ec.generate_private_key(ec.SECP256R1()),
        "rsa": lambda: rsa.generate_private_key(65537, 2048),
    }[kind]()
    enc = serialization.BestAvailableEncryption(passphrase.encode()) if passphrase else serialization.NoEncryption()
    text = priv.private_bytes(serialization.Encoding.PEM, fmt, enc).decode()
    public = (
        priv.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).split()[1]
    )
    return text, public


def test_the_defect_is_real_on_the_installed_paramiko():
    text, _ = make("ed25519", OPENSSH)
    with pytest.raises(TypeError, match="file_obj"):
        paramiko.PKey.from_private_key(io.StringIO(text))  # what the SFTP node used


UNPROTECTED = [
    ("ed25519", OPENSSH),
    ("ecdsa", OPENSSH),
    ("rsa", OPENSSH),
    ("rsa", PKCS1),
    ("rsa", PKCS8),
    ("ecdsa", PKCS8),
    ("ed25519", PKCS8),
]


@pytest.mark.parametrize("kind, fmt", UNPROTECTED)
def test_unprotected_keys_load_and_match_their_public_key(kind, fmt):
    text, public = make(kind, fmt)
    key = load_private_key(text)
    assert key is not None and key.get_base64().encode() == public


@pytest.mark.parametrize(
    "kind, fmt",
    [("ed25519", OPENSSH), ("ecdsa", OPENSSH), ("rsa", OPENSSH), ("rsa", PKCS1), ("rsa", PKCS8), ("ecdsa", PKCS8)],
)
def test_protected_keys_need_the_right_passphrase(kind, fmt):
    text, public = make(kind, fmt, PASSPHRASE)
    assert load_private_key(text, PASSPHRASE).get_base64().encode() == public
    assert load_private_key(text) is None and load_private_key(text, "mauvaise-phrase-0000") is None


@pytest.mark.parametrize(
    "junk",
    ["", "pas une clé", "-----" + "BEGIN OPENSSH PRIVATE" + " KEY-----\nxx\n-----END OPENSSH PRIVATE KEY" + "-----"],
)
def test_unreadable_text_is_none_not_an_exception(junk):
    assert load_private_key(junk) is None


def sftp_spec(name):
    return {
        "mode": "reel",
        "connection": name,
        "operation": "list",
        "path": "/",
        "content": None,
        "file_id": None,
        "binary": False,
        "timeout": 5,
    }


@pytest.mark.parametrize("kind, fmt", [("ed25519", OPENSSH), ("ecdsa", OPENSSH), ("rsa", PKCS1)])
def test_the_sftp_node_authenticates_with_a_private_key(ssh_ws, kind, fmt):
    text, _ = make(kind, fmt)
    key = load_private_key(text)
    with FakeSshd(users={"robot": {"password": None, "keys": [key]}}) as d:
        make_conn(ssh_ws, "depot", d, secret=make_secret(ssh_ws, "cle", text), kind="sftp")
        sftp_node.execute_spec(
            sftp_spec("depot"), ssh_ws, None, None
        )  # (the double has no SFTP subsystem: only the login is checked)
        assert d.connections[0]["auth"] == "publickey" and d.connections[0]["user"] == "robot"


def test_the_sftp_node_still_authenticates_with_a_password(ssh_ws):
    with FakeSshd(users={"robot": {"password": "pw-synthetique-1234", "keys": []}}) as d:
        make_conn(ssh_ws, "depot", d, secret=make_secret(ssh_ws, "pw", "pw-synthetique-1234"), kind="sftp")
        sftp_node.execute_spec(sftp_spec("depot"), ssh_ws, None, None)
        assert d.connections[0]["auth"] == "password"


def test_a_protected_key_is_refused_clearly_on_an_sftp_connection(ssh_ws):
    text, _ = make(
        "ed25519", OPENSSH, PASSPHRASE
    )  # an SFTP connection has no passphrase setting: not announced, so not guessed
    with FakeSshd(users={"robot": {"password": None, "keys": []}}) as d:
        make_conn(ssh_ws, "depot", d, secret=make_secret(ssh_ws, "cle", text), kind="sftp")
        out, err = sftp_node.execute_spec(sftp_spec("depot"), ssh_ws, None, None)
        assert out is None and err["code"] == "SFTP_AUTH" and "phrase secrète" in err["message"]
        assert PASSPHRASE not in repr(err) and d.connections[0]["auth"] is None
