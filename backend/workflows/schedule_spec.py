"""Contrat du nœud « Programmer » (ADR 0034) partagé par la validation (workflows) et l'exécution (runs.step_schedule).

Le nœud ne lance rien lui-même : il **programme** le lancement d'un autre workflow à une date et une heure (ou après un délai) ;
l'étape se termine tout de suite et le workflow en cours continue. Le lancement est fait plus tard par l'orchestrateur."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODES = ("at", "in")
ON_PAST = ("fail", "now")
DEFAULT_TIMEZONE = "Europe/Paris"
MAX_HORIZON_SECONDS = 366 * 86400
MAX_PENDING = 1000  # lancements en attente par espace et par environnement

STATIC_OUTPUTS = {
    "id": {"type": "string"},
    "workflow": {"type": "string"},
    "version": {"type": "string"},
    "run_at": {"type": "string"},
    "run_at_local": {"type": "string"},
    "immediate": {"type": "boolean"},
}


class WhenError(ValueError):
    """Date ou heure illisible : le message est montré tel quel."""


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise WhenError(f"Fuseau horaire inconnu : « {name} » (ex. Europe/Paris, UTC).") from None


def parse_when(value, tz_name: str | None = None) -> datetime:
    """`2026-12-01T08:30`, `2026-12-01 08:30:15`, ou avec un décalage (`…+01:00`, `…Z`) : la date ET l'heure sont exigées.
    Sans décalage, l'heure est celle du fuseau du nœud. Renvoie un instant en UTC."""
    if not isinstance(value, str) or not value.strip():
        raise WhenError("Indiquez la date et l'heure (ex. 2026-12-01T08:30).")
    text = value.strip()
    if len(text) <= 10:
        raise WhenError("Indiquez aussi l'heure, pas seulement la date (ex. 2026-12-01T08:30).")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise WhenError(f"Date illisible : « {text[:40]} ». Format attendu : 2026-12-01T08:30.") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=zone(tz_name))
    return when.astimezone(UTC)


def in_delay(now: datetime, seconds) -> datetime:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_HORIZON_SECONDS:
        raise WhenError("Délai : un nombre entier de secondes, de 1 seconde à 366 jours.")
    return now + timedelta(seconds=seconds)
