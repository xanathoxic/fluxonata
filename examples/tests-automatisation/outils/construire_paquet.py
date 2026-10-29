#!/usr/bin/env python3
"""Écrit l'archive du paquet d'actions (à importer depuis Bibliothèques → Actions → Paquets → Importer).

  python3 construire_paquet.py            # crée tests-automatisation-1.0.0.zip dans le dossier courant
"""

import sys
from pathlib import Path

from installer_ressources import paquet_zip

sortie = Path("tests-automatisation-1.0.0.zip")
sortie.write_bytes(paquet_zip())
print(f"{sortie} écrit ({sortie.stat().st_size} octets). Importez-le : Bibliothèques → Actions → Paquets.")
sys.exit(0)
