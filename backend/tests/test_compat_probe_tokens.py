"""Sonde de compatibilité « jeton personnel » (scripts/compat_probe_tokens.py) : le jugement et le comportement de la phase « new » (code courant)."""

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compat_probe_tokens.py"
spec = importlib.util.spec_from_file_location("compat_probe_tokens", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


EXPECTED = {"service": "sa-user", "personal": "person"}
OK_CONTROL = (200, "sa-user", None)
REFUSED = (401, None, None)


def test_a_clean_refusal_of_the_personal_token_conforms():
    ok, message = probe.judge(EXPECTED, OK_CONTROL, REFUSED, REFUSED)
    assert ok and "refusé" in message


def test_an_acceptance_conforms_only_under_the_identity_of_the_person():
    assert probe.judge(EXPECTED, OK_CONTROL, (200, "person", None), REFUSED)[0]
    ok, message = probe.judge(
        EXPECTED, OK_CONTROL, (200, "sa-user", None), REFUSED
    )  # a 200 under someone else's identity
    assert not ok and "AUTRE identité" in message
    assert not probe.judge(EXPECTED, OK_CONTROL, (200, None, None), REFUSED)[0]


@pytest.mark.parametrize("status", [500, 403, 404, 302])
def test_any_other_status_for_the_personal_token_is_a_gap(status):
    ok, message = probe.judge(EXPECTED, OK_CONTROL, (status, None, None), REFUSED)
    assert not ok and str(status) in message


@pytest.mark.parametrize("who", ["control", "personal", "invalid"])
def test_an_exception_is_a_gap_whichever_token_raised_it(who):
    answers = {"control": OK_CONTROL, "personal": REFUSED, "invalid": REFUSED}
    answers[who] = (None, None, "RelatedObjectDoesNotExist (tenancy/auth.py:40)")
    ok, message = probe.judge(EXPECTED, answers["control"], answers["personal"], answers["invalid"])
    assert not ok and "RelatedObjectDoesNotExist" in message


@pytest.mark.parametrize("control", [(401, None, None), (500, None, None), (200, "autre", None), (200, None, None)])
def test_a_failing_or_misattributed_control_token_means_the_probe_cannot_conform(control):
    ok, message = probe.judge(EXPECTED, control, REFUSED, REFUSED)
    assert not ok and "contrôle" in message


@pytest.mark.parametrize(
    "invalid", [(200, "person", None), (200, "sa-user", None), (500, None, None), (403, None, None)]
)
def test_an_unknown_token_must_be_refused_with_401(invalid):
    ok, message = probe.judge(EXPECTED, OK_CONTROL, REFUSED, invalid)
    assert not ok and "inconnu" in message


def test_usage_errors_exit_with_2():
    assert probe.main([]) == 2
    assert probe.main(["both", "x", "y"]) == 2


@pytest.mark.django_db
def test_the_new_phase_creates_a_personal_and_a_service_token_and_keeps_the_raw_values_private(tmp_path, capsys):
    from django.db import connection

    from tenancy.auth import hash_token
    from tenancy.models import ApiToken

    state = tmp_path / "state.json"
    backend = str(Path(__file__).resolve().parents[1])
    assert probe.phase_new(backend, str(state)) == 0
    saved = json.loads(state.read_text())
    raws = saved["tokens"]
    assert saved["users"] == {"personal": "compat-probe-person", "service": "compat-probe-sa"}
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o600
    out = capsys.readouterr().out
    assert raws["personal"] not in out and raws["service"] not in out  # raw tokens are never printed
    personal = ApiToken.objects.get(token_hash=hash_token(raws["personal"]))
    service = ApiToken.objects.get(token_hash=hash_token(raws["service"]))
    assert personal.user_id is not None and personal.service_account_id is None
    assert service.service_account_id is not None and service.user_id is None
    assert connection.vendor == "postgresql"


def test_the_settings_module_is_read_from_the_manage_py_of_the_version_under_test(tmp_path):
    (tmp_path / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'autrenom.settings')\n")
    assert probe.settings_module(str(tmp_path)) == "autrenom.settings"
    assert probe.settings_module(str(tmp_path / "absent")) == "fluxonata.settings"  # no manage.py: current name
    real = Path(__file__).resolve().parents[1]
    assert probe.settings_module(str(real)) == "fluxonata.settings"
