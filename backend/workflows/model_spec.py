"""Contrat du nœud « Modèle de document » (ADR 0036) partagé par la validation (workflows) et l'exécution (runs.step_model)."""

MAX_TITLE = 200
STATIC_OUTPUTS = {
    "title": {"type": "string"},
    "markdown": {"type": "string"},
    "html": {"type": "string"},
    "text": {"type": "string"},
    "images": {"type": "array"},
}
