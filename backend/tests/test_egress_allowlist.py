import pytest
from django.contrib.auth import get_user_model

from audit.models import AuditEvent
from core import netsec
from core.netsec import UnsafeURL, validate_url
from tenancy.models import EgressHost
from tests.conftest import client_for, make_user

P = "/api/v1/platform/egress/"
User = get_user_model()


@pytest.fixture(autouse=True)
def fresh_cache():
    netsec.reset_allowlist_cache()
    yield
    netsec.reset_allowlist_cache()


@pytest.fixture
def boss(db):
    u = make_user("root")
    u.is_superuser = True
    u.save()
    return u


def test_a_platform_administrator_allows_and_removes_an_internal_host_which_applies_at_once(boss, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "10.9.9.9"
    c = client_for(boss)
    with pytest.raises(UnsafeURL):
        validate_url("http://127.0.0.1:8765/marques")
    r = c.post(P, {"host": " 127.0.0.1 ", "note": "API de démonstration"}, format="json")
    assert r.status_code == 201 and r.data["host"] == "127.0.0.1" and r.data["created_by"] == "root"
    assert validate_url("http://127.0.0.1:8765/marques").host == "127.0.0.1"
    listed = c.get(P).data
    assert listed["environment"] == ["10.9.9.9"] and [h["host"] for h in listed["results"]] == ["127.0.0.1"]
    assert c.delete(f"{P}{r.data['id']}/").status_code == 204
    with pytest.raises(UnsafeURL):
        validate_url("http://127.0.0.1:8765/marques")
    assert c.delete(f"{P}{r.data['id']}/").status_code == 404
    events = list(AuditEvent.objects.filter(action__startswith="platform.egress").order_by("id"))
    assert [e.action for e in events] == ["platform.egress_add", "platform.egress_remove"]
    assert events[0].actor_label == "root" and events[0].data["host"] == "127.0.0.1"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://127.0.0.1",
        "127.0.0.1:8765",
        "demo.example.com/path",
        "*.example.com",
        "10.0.0.0/8",
        "a,b",
        "has space",
        "user@host",
        "-bad.example.com",
        "x" * 254,
        "169.254.169.254",  # cloud metadata
        "0.0.0.0",
        "224.0.0.1",
        "fe80::1",
        "::ffff:169.254.169.254",
        "metadata.google.internal",
    ],
)
def test_only_a_name_or_an_ip_is_accepted_and_never_a_metadata_or_reserved_address(boss, bad):
    r = client_for(boss).post(P, {"host": bad}, format="json")
    assert r.status_code == 400 and "host" in r.data["error"]["details"]
    assert EgressHost.objects.count() == 0


@pytest.mark.parametrize(
    "good,stored",
    [
        ("10.1.2.3", "10.1.2.3"),
        ("LocalHost", "localhost"),
        ("svc.internal.example", "svc.internal.example"),
        ("[::1]", "::1"),
    ],
)
def test_internal_services_may_be_allowed(boss, good, stored):
    r = client_for(boss).post(P, {"host": good}, format="json")
    assert r.status_code == 201 and r.data["host"] == stored


def test_duplicates_and_the_environment_list_are_refused_and_the_list_is_bounded(boss, settings, monkeypatch):
    settings.ORCH_EGRESS_ALLOWLIST = "10.9.9.9"
    c = client_for(boss)
    assert c.post(P, {"host": "10.9.9.9"}, format="json").status_code == 400  # already allowed by the operator
    assert c.post(P, {"host": "10.1.1.1"}, format="json").status_code == 201
    assert c.post(P, {"host": "10.1.1.1"}, format="json").status_code == 400
    monkeypatch.setattr("tenancy.platform_views.MAX_EGRESS_HOSTS", 1)
    assert c.post(P, {"host": "10.1.1.2"}, format="json").status_code == 400


def test_a_name_added_in_the_interface_that_answers_with_a_metadata_address_is_still_refused(
    boss, settings, monkeypatch
):
    monkeypatch.setattr(netsec, "resolve", lambda host: ["169.254.169.254"])
    assert client_for(boss).post(P, {"host": "rebind.example.com"}, format="json").status_code == 201
    with pytest.raises(UnsafeURL):
        validate_url("http://rebind.example.com/latest/meta-data")
    settings.ORCH_EGRESS_ALLOWLIST = "rebind.example.com"  # the operator's own list stays the operator's responsibility
    assert validate_url("http://rebind.example.com/x").host == "rebind.example.com"


def test_only_platform_administrators_may_change_it_and_a_workspace_administrator_may_not(world):
    for who in ("designer", "outsider"):
        c = client_for(world[who])
        assert c.post(P, {"host": "10.1.1.1"}, format="json").status_code == 403
    assert EgressHost.objects.count() == 0
