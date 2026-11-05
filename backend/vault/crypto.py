"""Secret encryption at rest with key management outside the database and the repository.

Keys live in ORCH_KEY_DIR (default ~/.config/fluxonata/keys), one Fernet key per file (mode 0600), `current` names
the active key. Decryption tries every key present, so rotation is: generate a new key, re-encrypt, then retire
the old key file. Production deployments should mount the directory from a secrets manager / tmpfs.

First key (AR2.5) : en développement et en test, la première clé est créée toute seule au premier usage (ORCH_KEY_AUTOCREATE=1, défaut). **En production
(ORCH_KEY_AUTOCREATE=0), jamais** : un process démarré sans le volume des clés — ou avec un volume vide monté par erreur — fabriquerait sinon une clé neuve,
y chiffrerait les nouveaux secrets, et cette clé disparaîtrait avec son conteneur (secrets irrécupérables). La clé se crée alors explicitement, une fois,
par `manage.py vault_init` (aussi lancé par `bootstrap`), et tout autre process échoue franchement tant qu'elle manque.
"""

import json
import os
import secrets as pysecrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class VaultError(Exception):
    pass


def autocreate() -> bool:
    return bool(getattr(settings, "ORCH_KEY_AUTOCREATE", True))


def key_dir(create: bool | None = None) -> Path:
    d = Path(
        os.environ.get("ORCH_KEY_DIR")
        or getattr(settings, "ORCH_KEY_DIR", "")
        or Path.home() / ".config" / "fluxonata" / "keys"
    )
    if create or (create is None and autocreate()):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not d.is_dir():
        raise VaultError(
            f"Le dossier des clés de chiffrement est absent ({d}) : le volume des clés n'est pas monté dans ce service. "
            "Rien n'est créé automatiquement en production ; montez le volume, ou créez la première clé avec `manage.py vault_init`."
        )
    return d


def _read_keys() -> dict[str, bytes]:
    return {p.stem[4:]: p.read_bytes().strip() for p in sorted(key_dir().glob("key-*.key"))}


def current_key_id() -> str:
    cur = key_dir() / "current"
    if not cur.exists():
        if not autocreate():
            raise VaultError(
                "Aucune clé de chiffrement dans ce dossier. Elle n'est PAS créée automatiquement en production (une clé fabriquée "
                "par erreur rendrait les secrets irrécupérables) : lancez une fois `manage.py vault_init`, ou montez le bon volume."
            )
        return new_key()
    return cur.read_text().strip()


def ensure_key() -> tuple[str, bool]:
    """Crée la première clé si le dossier n'en a aucune. Retourne (identifiant, créée ?). Ne touche jamais une clé existante."""
    d = key_dir(create=True)
    cur = d / "current"
    if cur.exists() and (d / f"key-{cur.read_text().strip()}.key").exists():
        return cur.read_text().strip(), False
    if any(d.glob("key-*.key")):
        raise VaultError(
            "Des clés existent mais aucune n'est désignée « current » : réparez à la main, rien n'est écrasé."
        )
    return new_key(), True


def new_key() -> str:
    kid = pysecrets.token_hex(4)
    path = key_dir(create=True) / f"key-{kid}.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(Fernet.generate_key())
    (key_dir(create=True) / "current").write_text(kid)
    return kid


def _fernet() -> MultiFernet:
    cur = current_key_id()
    keys = _read_keys()
    if cur not in keys:
        raise VaultError("Clé de chiffrement courante introuvable.")
    ordered = [keys[cur]] + [k for kid, k in keys.items() if kid != cur]
    return MultiFernet([Fernet(k) for k in ordered])


def encrypt(value: str, purpose: str) -> tuple[str, str]:
    """Returns (key_id, token). `purpose` is bound into the payload to prevent cross-use of ciphertexts."""
    payload = json.dumps({"p": purpose, "v": value}).encode()
    return current_key_id(), _fernet().encrypt(payload).decode()


def decrypt(token: str, purpose: str) -> str:
    try:
        data = json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken:
        raise VaultError("Secret illisible (clé absente ou donnée corrompue).") from None
    if data.get("p") != purpose:
        raise VaultError("Usage du secret non autorisé.")
    return data["v"]


def reencrypt(token: str, purpose: str) -> tuple[str, str]:
    return encrypt(decrypt(token, purpose), purpose)


def retire_old_keys() -> list[str]:
    cur = current_key_id()
    removed = []
    for kid in list(_read_keys()):
        if kid != cur:
            (key_dir() / f"key-{kid}.key").unlink()
            removed.append(kid)
    return removed
