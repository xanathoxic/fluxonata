"""Réglages des tests : ceux de production, avec l'extension « API store » activée pour que ses tests s'exécutent."""

import os

os.environ.setdefault("ORCH_EXTENSIONS", "apistore")

from .settings import *  # noqa: E402, F403
