"""Découverte des extensions : lit `ORCH_EXTENSIONS`, vérifie chaque manifeste, ne plante jamais le démarrage.

Appelé depuis les réglages Django, donc AVANT que les applications soient prêtes : ce module n'importe ni modèle ni autre application.
"""

import importlib
import re
from dataclasses import dataclass

from .manifest import Manifest, ManifestError, parse_manifest

MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True)
class Extension:
    manifest: Manifest
    module: str  # paquet Python (aussi le nom de l'application Django)


@dataclass(frozen=True)
class Refusal:
    name: str
    reason: str


def _load_one(name: str) -> Extension:
    if not MODULE_RE.match(name):
        raise ManifestError("nom de paquet Python invalide.")
    try:
        plugin = importlib.import_module(f"{name}.plugin")
    except ImportError:
        raise ManifestError(f"paquet introuvable ou sans module « {name}.plugin ».") from None
    manifest = parse_manifest(getattr(plugin, "MANIFEST", None))
    return Extension(manifest, name)


def discover(names: str) -> tuple[list[Extension], list[Refusal]]:
    """Les extensions acceptées et celles refusées avec leur raison. Un manifeste fautif ou un doublon n'arrête rien."""
    found: list[Extension] = []
    refused: list[Refusal] = []
    for name in dict.fromkeys(n.strip() for n in names.split(",") if n.strip()):
        try:
            ext = _load_one(name)
        except ManifestError as exc:
            refused.append(Refusal(name, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - une extension qui plante à l'import est refusée, jamais fatale pour le cœur
            refused.append(Refusal(name, f"erreur au chargement ({type(exc).__name__})."))
            continue
        if any(e.manifest.id == ext.manifest.id for e in found):
            refused.append(Refusal(name, f"identifiant « {ext.manifest.id} » déjà pris par une autre extension."))
            continue
        found.append(ext)
    return found, refused
