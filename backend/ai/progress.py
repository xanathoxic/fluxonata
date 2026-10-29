"""Progress of a long assistant job (generating a draft): what phase it is in, shown live to the person.

The service code calls `report(...)` at each phase; outside a job nothing is listening and the call does nothing, so the
synchronous paths (tests, scripts, the benchmark) are unchanged. A job installs a sink that appends the event to the
job row, which the editor polls."""

import contextvars
import time

# phase -> (share of the whole job reached when the phase starts, label shown while it runs)
PHASES = {
    "context": (2, "Préparation du contexte (catalogue, workflows, skills)"),
    "plan": (6, "Le modèle rédige le plan du workflow"),
    "build": (38, "Construction du schéma à partir du plan"),
    "check": (44, "Contrôle de cohérence (liaisons, entrées/sorties, scripts)"),
    "repair": (52, "Correction automatique des problèmes trouvés"),
    "dryrun": (60, "Essai à blanc : exécution des scripts et des conditions"),
    "analysis": (76, "Analyse des résultats de l'essai"),
    "review": (82, "Relecture indépendante : le résultat répond-il à la demande ?"),
    "save": (94, "Enregistrement du brouillon"),
    "done": (100, "Terminé"),
}

_sink: contextvars.ContextVar = contextvars.ContextVar("ai_progress_sink", default=None)


def install(sink):
    """`sink(event: dict)` receives every event; returns a token for `uninstall`."""
    return _sink.set(sink)


def uninstall(token) -> None:
    _sink.reset(token)


def report(phase: str, detail: str = "", state: str = "running", label: str | None = None, pct: int | None = None):
    """state: running | done | warning | error. `detail` is a short plain sentence for the live log."""
    sink = _sink.get()
    if sink is None:
        return
    start, default_label = PHASES.get(phase, (0, phase))
    sink(
        {
            "t": round(time.time(), 1),
            "phase": phase,
            "label": label or default_label,
            "state": state,
            "detail": detail[:300],
            "pct": pct if pct is not None else start,
        }
    )
