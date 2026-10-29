from catalog.models import Action, ActionVersion

AI_SLUG = "__ai_step__"


def get_ai_version(workspace) -> ActionVersion:
    """Hidden built-in action backing 'AI step' nodes: it runs in the worker through the provider interface
    (no container, no code), so leases, cancellation, retries and audit work exactly like any other step."""
    action, _ = Action.objects.get_or_create(
        workspace=workspace, slug=AI_SLUG, defaults={"name": "Étape IA", "description": "Intégré"}
    )
    v = action.versions.filter(version="1.0.0").first()
    if v is None:
        v = ActionVersion.objects.create(
            action=action,
            version="1.0.0",
            runtime="ai",
            source="",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effects="none",
            idempotent=True,
            network_policy="none",
            limits={"timeout_seconds": 120, "memory_mb": 64},
        )
    return v
