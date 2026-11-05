"""AR5.4 — équité entre espaces : un espace qui sature la file ne prive pas les autres.

Défaut reproduit avant correction : `claim_next` ne regardait que les 200 exécutions les plus anciennes TOUS espaces confondus. Un espace au plafond de parallélisme avec plus de
200 tâches en file remplissait cette fenêtre de tâches inéligibles ; l'exécution d'un autre espace, plus récente, n'était jamais prise tant que le premier n'avait pas drainé."""

import pytest
from django.utils import timezone

from catalog.models import ActionVersion
from runs import services
from runs.models import Execution
from tenancy.models import Environment
from tests.conftest import make_user
from tests.wf_helpers import ACTIONS, make_action


@pytest.fixture
def two_spaces(world):
    a, b = world["ws_a"], world["ws_b"]
    for ws in (a, b):
        for slug in ACTIONS:
            make_action(ws, slug)
    return a, b


def enqueue(ws, n, user, **kw):
    version = ActionVersion.objects.filter(action__workspace=ws, action__slug="echo").first()
    env = Environment.objects.get(workspace=ws, kind="test")
    return [
        services.enqueue(
            workspace=ws, environment=env, action_version=version, user=user, inputs={"value": str(i)}, **kw
        )
        for i in range(n)
    ]


def test_a_saturating_space_at_its_cap_does_not_starve_another_one(two_spaces, world, settings):
    a, b = two_spaces
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 2
    user = world["designer"]
    other = make_user("betty", b)
    enqueue(a, 250, user)  # 250 tâches en file pour A, toutes plus anciennes que celle de B
    first = [services.claim_next("w1"), services.claim_next("w2")]
    assert all(c and c[0].workspace_id == a.pk for c in first)  # A est maintenant à son plafond (2 en cours)
    (b_exec,) = enqueue(b, 1, other)
    got = services.claim_next("w3")
    assert got is not None and got[0].pk == b_exec.pk, (
        "l'exécution de l'espace B n'est pas prise alors que A est au plafond : famine"
    )


def test_the_least_loaded_space_is_served_first_even_behind_a_long_backlog(two_spaces, world, settings):
    a, b = two_spaces
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 8
    enqueue(a, 300, world["designer"])
    assert services.claim_next("w1")[0].workspace_id == a.pk  # A a 1 exécution en cours
    (b_exec,) = enqueue(b, 1, make_user("betty", b))
    assert (
        services.claim_next("w2")[0].pk == b_exec.pk
    )  # B (0 en cours) passe devant la suite du carnet de A (1 en cours)


def test_priority_still_wins_over_load(two_spaces, world, settings):
    a, b = two_spaces
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 8
    enqueue(a, 3, world["designer"], priority=5)
    services.claim_next("w1")
    enqueue(b, 1, make_user("betty", b))
    nxt = services.claim_next("w2")[0]
    assert (
        nxt.workspace_id == a.pk and nxt.priority == 5
    )  # une priorité plus haute passe avant l'équité de charge (comportement existant)


def test_nothing_is_claimed_twice_and_the_cap_is_respected_under_a_long_queue(two_spaces, world, settings):
    a, b = two_spaces
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 3
    enqueue(a, 230, world["designer"])
    enqueue(b, 5, make_user("betty", b))
    claimed = []
    for i in range(20):
        c = services.claim_next(f"w{i}")
        if c:
            claimed.append(c[0])
    assert len({c.pk for c in claimed}) == len(claimed)
    per_ws = {ws.pk: sum(1 for c in claimed if c.workspace_id == ws.pk) for ws in (a, b)}
    assert per_ws[a.pk] == 3 and per_ws[b.pk] == 3  # chaque espace a atteint son plafond, aucun ne dépasse
    assert Execution.objects.filter(status="running").count() == 6


def test_window_does_not_depend_on_the_global_age_of_a_foreign_backlog(two_spaces, world, settings):
    """Même avec 600 tâches plus anciennes dans un autre espace, la prise reste correcte (et rapide : la requête ne charge pas tout le carnet)."""
    a, b = two_spaces
    settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE = 1
    enqueue(a, 600, world["designer"])
    services.claim_next("w1")  # A au plafond (1)
    (b_exec,) = enqueue(b, 1, make_user("betty", b))
    t0 = timezone.now()
    got = services.claim_next("w2")
    assert got[0].pk == b_exec.pk and (timezone.now() - t0).total_seconds() < 2
    assert settings.ORCH_MAX_CONCURRENT_PER_WORKSPACE == 1 and getattr(settings, "ORCH_FAIR_WINDOW", 20) >= 1
