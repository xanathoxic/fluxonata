"""Extension points of the vault: what other applications plug in, so that `vault` imports none of them.

- connection checks: a kind of connection whose settings are validated by the application that runs it (SMTP, SSH);
- key rotation: encrypted columns that live in other applications (webhook secrets) are re-encrypted by them.

Each application registers its own from its `AppConfig.ready()`."""

from collections.abc import Callable


class ConfigRefused(Exception):
    """A connection's settings are refused; the message is meant for the person who typed them."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_config_checks: dict[str, Callable[[dict], None]] = {}
_rotations: list[Callable[[], int]] = []


def register_config_check(kind: str, check: Callable[[dict], None]) -> None:
    """`check(config)` raises `ConfigRefused` when the settings of a connection of this kind are not acceptable."""
    _config_checks[kind] = check


def check_config(kind: str, config: dict) -> None:
    check = _config_checks.get(kind)
    if check is not None:
        check(config)


def register_rotation(rotate: Callable[[], int]) -> None:
    """`rotate()` re-encrypts the columns of the registering application with the newest key and returns how many it did."""
    _rotations.append(rotate)


def rotate_registered() -> int:
    return sum(rotate() for rotate in _rotations)
