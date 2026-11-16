"""Points d'extension du coffre : `vault` n'importe ni `runs` ni `triggers`, ceux-ci s'y inscrivent au démarrage."""

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from tenancy.models import Environment
from tests.wf_helpers import node, publish
from triggers.models import Webhook
from vault import registry

BASE = "/api/v1/workspaces/team-a/connections/"


def test_the_steps_register_their_connection_checks_at_startup():
    assert {"smtp", "ssh"} <= set(registry._config_checks)


def test_a_kind_without_check_is_accepted_and_a_refusal_reaches_the_person(monkeypatch):
    registry.check_config("postgres", {"anything": 1})  # nothing registered for it: not this module's business
    monkeypatch.setitem(registry._config_checks, "x", lambda c: (_ for _ in ()).throw(registry.ConfigRefused("non")))
    with pytest.raises(registry.ConfigRefused, match="non"):
        registry.check_config("x", {})


def test_an_invalid_smtp_or_ssh_setting_is_refused_through_the_api(world):
    c = APIClient()
    c.force_authenticate(world["designer"])
    for kind, config in (("smtp", {"host": "", "port": "abc"}), ("ssh", {"port": "pas-un-port", "host": "h"})):
        r = c.post(
            BASE,
            {"name": f"c-{kind}", "label": "x", "kind": kind, "config": config, "secret_value": "v"},
            format="json",
        )
        assert r.status_code == 400 and "config" in r.json()["error"]["details"], (kind, r.content)


def test_rotating_the_keys_reencrypts_webhook_secrets_too(world):
    ws = world["ws_a"]
    v = publish(ws, "wh", [node("a", "wait", seconds=1)], [], user=world["designer"])
    hook = Webhook(
        workspace=ws, workflow=v.workflow, environment=Environment.objects.get(workspace=ws, kind="test"), name="h"
    )
    hook.set_secret("secret-du-webhook")
    hook.save()
    before = hook.key_id
    call_command("rotate_keys")
    hook.refresh_from_db()
    assert hook.key_id != before and hook.reveal_secret() == "secret-du-webhook"
    assert registry.rotate_registered() >= 1  # the hook registered by `triggers` is the one that ran
