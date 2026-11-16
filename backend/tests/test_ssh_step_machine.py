"""Caractérisation de `runs.ssh_shell._step` : la machine à états d'une étape de session (envoyer, attendre, répondre aux invites, conclure).

Un canal SIMULÉ remplace le serveur : il livre des morceaux de sortie à des moments choisis, ce qui rend chaque issue déterministe et rapide
(les tests avec un vrai serveur SSH jetable restent dans test_ssh_shell.py). Écrit avant le découpage de la fonction : tout doit passer à
l'identique avant et après."""

import time

import pytest

from runs import ssh_shell
from runs.ssh_core import MASKED, SshStepError
from runs.ssh_shell import _Channel, _step


class StubChan:
    """Un canal paramiko minimal : `script` est une liste de morceaux (octets) livrés un par lecture ; `on_send` réagit à un envoi."""

    def __init__(self, script=(), on_send=None, fail_send=False, eof=False):
        self.script = list(script)
        self.err_script = []
        self.sent = []
        self.on_send, self.fail_send = on_send, fail_send
        self.closed, self.eof_received = False, eof

    def recv_ready(self):
        return bool(self.script)

    def recv(self, n):
        return self.script.pop(0) if self.script else b""

    def recv_stderr_ready(self):
        return bool(self.err_script)

    def recv_stderr(self, n):
        return self.err_script.pop(0) if self.err_script else b""

    def send(self, data):
        if self.fail_send:
            raise OSError("fermé")
        self.sent.append(data.decode())
        if self.on_send:
            self.on_send(self, data.decode())


def make_step(**kw):
    base = {
        "id": "s1",
        "send": None,
        "send_secret": None,
        "eol": "lf",
        "expect": [],
        "failure": [],
        "success": [],
        "responses": [],
        "timeout": None,
        "sensitive": False,
        "ignore_error": False,
    }
    return base | kw


SPEC = {"op_timeout": 5, "max_output": 1000}


def run(step, chan, *, deadline=None, cancel=lambda: False, combined=False, spec=SPEC, secrets=None):
    deadline = deadline if deadline is not None else time.monotonic() + 30
    channel = _Channel(chan, spec["max_output"])
    res, abort = _step(channel, step, spec, secrets or {}, deadline, cancel, combined)
    return res, abort, channel


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(ssh_shell, "POLL", 0.001)


def test_a_step_that_starts_after_the_global_deadline_is_not_run():
    res, abort, _ = run(make_step(expect=["x"]), StubChan(), deadline=time.monotonic() - 1)
    assert res["status"] == "not_run"
    assert isinstance(abort, SshStepError) and abort.code == "SSH_TIMEOUT"


def test_a_cancelled_step_sends_nothing():
    chan = StubChan()
    res, abort, _ = run(make_step(send="rm -rf /", expect=["x"]), chan, cancel=lambda: True)
    assert chan.sent == [] and res["status"] == "not_run" and abort.code == "SSH_CANCELLED"


def test_a_send_only_step_succeeds_without_waiting():
    chan = StubChan(script=[b"bruit avant"])
    res, abort, _ = run(make_step(send="ls"), chan)
    assert chan.sent == ["ls\n"] and res["status"] == "ok" and res["sent"] == "ls" and abort is None


def test_what_arrived_before_the_send_never_satisfies_the_wait():
    chan = StubChan(script=[b"vieux prompt> "], on_send=lambda c, text: c.script.append(b"resultat\nprompt> "))
    res, _, _ = run(make_step(send="cmd", expect=[r"prompt> $"]), chan)
    assert res["status"] == "ok" and "vieux" not in res["stdout"] and "resultat" in res["stdout"]


def test_a_send_refused_by_a_closed_session_is_a_closed_failure_that_is_not_uncertain():
    res, abort, _ = run(make_step(send="x", expect=["y"]), StubChan(fail_send=True))
    assert res["status"] == "failed" and res["error"]["type"] == "closed" and "avant l'envoi" in res["error"]["message"]
    assert res["uncertain"] is False and abort is None


def test_the_expected_pattern_ends_the_step_and_what_follows_stays_for_the_next_one():
    chan = StubChan(script=[b"debut\nprompt> reste"])
    res, _, channel = run(make_step(expect=[r"prompt> "]), chan)
    assert res["status"] == "ok" and res["stdout"] == "debut\nprompt> " and channel.pending == "reste"
    assert res["matched"] == {"kind": "expect", "pattern": r"prompt> "}


def test_a_success_pattern_is_required_when_declared():
    ok, _, _ = run(make_step(expect=["fin"], success=["OK"]), StubChan(script=[b"OK fin"]))
    assert ok["status"] == "ok"
    bad, _, _ = run(make_step(expect=["fin"], success=["OK"]), StubChan(script=[b"KO fin"]))
    assert (
        bad["status"] == "failed"
        and bad["error"]["type"] == "remote_status"
        and "motif de succès" in bad["error"]["message"]
    )


def test_a_failure_pattern_wins_and_is_reported_with_the_pattern():
    res, _, _ = run(make_step(expect=["fin"], failure=["ERROR"]), StubChan(script=[b"ERROR: boom\nfin"]))
    assert res["status"] == "failed" and res["matched"] == {"kind": "failure", "pattern": "ERROR"}
    assert res["error"]["message"] == "Motif d'échec reconnu dans la sortie."


def test_a_prompt_gets_its_answer_up_to_its_maximum_then_the_step_goes_on():
    def on_send(chan, text):
        if text.startswith("pw"):
            chan.script.append(b"Password: ")  # the server asks again
        elif text.startswith("fin"):
            chan.script.append(b"done")

    chan = StubChan(script=[b"Password: "], on_send=on_send)
    step = make_step(
        send=None,
        expect=["done"],
        responses=[
            {"expect": "Password: ", "send": "pw", "send_secret": None, "eol": "lf", "max": 2, "sensitive": False}
        ],
    )
    # the answer is sent twice at most; the third prompt is not answered, so the step waits for « done » until it times out
    step["timeout"] = 1
    chan.script.append(b"Password: ")
    res, _, _ = run(step, chan, spec={"op_timeout": 1, "max_output": 1000})
    assert chan.sent == ["pw\n", "pw\n"]
    assert [i["sent"] for i in res["interactions"]] == ["pw", "pw"]
    assert res["status"] == "timeout" and res["timed_out"] is True


def test_a_secret_answer_is_referenced_never_shown():
    resp = {"expect": "Password: ", "send": None, "send_secret": "pw", "eol": "lf", "max": 1, "sensitive": True}
    chan = StubChan(script=[b"Password: "], on_send=lambda c, t: c.script.append(b"ok>"))
    res, _, _ = run(make_step(expect=["ok>"], responses=[resp]), chan, secrets={"pw": "s3cret"})
    assert chan.sent == ["s3cret\n"] and res["interactions"][0]["sent"] == "<secret:pw>"
    assert "s3cret" not in repr(res)


def test_a_sensitive_answer_is_masked_in_the_interactions():
    resp = {"expect": "Q> ", "send": "valeur", "send_secret": None, "eol": "none", "max": 1, "sensitive": True}
    chan = StubChan(script=[b"Q> "], on_send=lambda c, t: c.script.append(b"fin"))
    res, _, _ = run(make_step(expect=["fin"], responses=[resp]), chan)
    assert res["interactions"][0]["sent"] == MASKED and chan.sent == ["valeur"]


def test_an_answer_refused_by_a_closed_session_is_an_uncertain_failure():
    resp = {"expect": "Q> ", "send": "r", "send_secret": None, "eol": "lf", "max": 1, "sensitive": False}
    chan = StubChan(script=[b"Q> "], fail_send=True)
    res, _, _ = run(make_step(expect=["fin"], responses=[resp]), chan)
    assert (
        res["status"] == "failed"
        and res["error"]["type"] == "closed"
        and "pendant une interaction" in res["error"]["message"]
    )
    assert res["uncertain"] is False  # nothing was sent before: the uncertainty only exists once something was


def test_a_step_cancelled_while_waiting_ends_cancelled_and_aborts_the_run():
    calls = iter([False, False, True])
    res, abort, _ = run(make_step(send="long", expect=["jamais"]), StubChan(), cancel=lambda: next(calls, True))
    assert res["status"] == "failed" and res["error"]["type"] == "cancelled" and res["uncertain"] is True
    assert abort.code == "SSH_CANCELLED"


def test_a_wait_that_is_not_satisfied_times_out_with_its_limit_in_the_message():
    res, _, _ = run(
        make_step(send="x", expect=["jamais"], timeout=1), StubChan(), spec={"op_timeout": 1, "max_output": 1000}
    )
    assert res["status"] == "timeout" and res["timed_out"] is True and res["uncertain"] is True
    assert res["error"]["message"] == "Attente non satisfaite après 1 s."


def test_a_session_closed_by_the_server_before_the_wait_is_satisfied_is_a_closed_failure():
    res, _, _ = run(make_step(expect=["jamais"]), StubChan(eof=True))
    assert (
        res["status"] == "failed"
        and res["error"]["type"] == "closed"
        and "avant la fin de l'attente" in res["error"]["message"]
    )


def test_data_that_arrives_together_with_the_close_is_still_evaluated():
    chan = StubChan(script=[b"tout est la: fin"], eof=True)
    res, _, _ = run(make_step(expect=["fin"]), chan)
    assert res["status"] == "ok"


def test_a_flood_of_output_ends_the_step_with_the_output_limit():
    chan = StubChan(script=[b"x" * 70000] * 3)
    res, _, _ = run(make_step(send="big", expect=["jamais"]), chan, spec={"op_timeout": 5, "max_output": 100})
    assert res["status"] == "failed" and res["error"]["type"] == "output_limit" and res["truncated"] is True
    assert len(res["stdout"]) <= 100 and res["uncertain"] is True


def test_the_text_kept_for_a_failed_step_includes_what_matched_nothing():
    res, _, _ = run(
        make_step(expect=["fin"], timeout=1),
        StubChan(script=[b"quelque chose"]),
        spec={"op_timeout": 1, "max_output": 1000},
    )
    assert res["status"] == "timeout" and res["stdout"] == "quelque chose"


def test_stderr_is_kept_separately_or_emptied_when_the_streams_are_combined():
    chan = StubChan(script=[b"a fin"])
    chan.err_script = [b"erreur"]
    separate, _, _ = run(make_step(expect=["fin"]), chan)
    assert separate["stderr"] == "erreur" and separate["streams"] == "separate"
    chan2 = StubChan(script=[b"a fin"])
    chan2.err_script = [b"erreur"]
    combined, _, _ = run(make_step(expect=["fin"]), chan2, combined=True)
    assert combined["stderr"] == "" and combined["streams"] == "combined"


def test_a_sensitive_step_never_returns_its_output():
    res, _, _ = run(
        make_step(send="secret-cmd", expect=["fin"], sensitive=True),
        StubChan(script=[b"mot de passe fin"], on_send=None),
    )
    assert res["redacted"] is True and res["stdout"] is None and res["sent"] is None


def test_a_pattern_too_costly_to_evaluate_ends_the_step_as_a_configuration_error(monkeypatch):
    monkeypatch.setattr(ssh_shell, "SEARCH_BUDGET", -1.0)
    res, abort, _ = run(make_step(expect=["fin"]), StubChan(script=[b"fin"]))
    assert res["status"] == "failed" and res["error"]["type"] == "config" and abort is None
