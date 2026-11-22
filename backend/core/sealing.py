"""Chiffrement des secrets pour les modules qui ne dépendent pas du coffre (comme `core.netsec` : aucune application importée).

Le coffre (`vault.crypto`) enregistre ici ses deux fonctions au démarrage ; un module qui doit ranger un secret en base (le secret d'un client
SSO, par exemple) appelle `seal` / `unseal` sans connaître le coffre. L'usage (`purpose`) est lié au texte chiffré : un secret chiffré pour un
usage ne se déchiffre pas pour un autre."""

from collections.abc import Callable

_encrypt: Callable[[str, str], tuple[str, str]] | None = None
_decrypt: Callable[[str, str], str] | None = None


def register(encrypt: Callable[[str, str], tuple[str, str]], decrypt: Callable[[str, str], str]) -> None:
    global _encrypt, _decrypt
    _encrypt, _decrypt = encrypt, decrypt


def seal(value: str, purpose: str) -> tuple[str, str]:
    """(identifiant de la clé, texte chiffré)."""
    if _encrypt is None:
        raise RuntimeError("Le coffre n'est pas chargé : aucun chiffrement disponible.")
    return _encrypt(value, purpose)


def unseal(token: str, purpose: str) -> str:
    if _decrypt is None:
        raise RuntimeError("Le coffre n'est pas chargé : aucun chiffrement disponible.")
    return _decrypt(token, purpose)
