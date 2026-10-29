"""Folder paths ("Dossier/Sous-dossier") shared by workflows, actions and AI skills."""

from rest_framework import serializers

MAX_FOLDER = 200
MAX_PART = 60


def clean_folder(raw) -> str:
    """Normalise a folder path: trimmed segments joined by "/", bounded length. Raises a field error when invalid."""
    folder = "/".join(p.strip() for p in str(raw or "").split("/") if p.strip())[:MAX_FOLDER]
    if any(len(p) > MAX_PART for p in folder.split("/")):
        raise serializers.ValidationError({"folder": f"Chaque dossier : {MAX_PART} caractères maximum."})
    return folder
