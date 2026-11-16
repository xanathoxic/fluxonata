"""Caractérisation de `runs.ssh_core._exec_one` : l'exécution d'UNE commande distante (ouvrir un canal, lancer, lire les deux flux, borner,
annuler, conclure par le statut de sortie). Un canal SIMULÉ rend chaque issue déterministe et rapide. Écrit avant le découpage de la
fonction : tout doit passer à l'identique avant et après."""

import time

import pytest

from runs import ssh_core
from runs.ssh_core import MASKED, MAX_TOTAL, _exec_one


class StubExec:
    """Un canal d'exécution paramiko minimal. `chunks` : octets de stdout livrés un par lecture ; `status` : statut de sortie (None : aucun)."""

    def __init__(self, chunks=(), err=(), status=0, exec_error=None, eof_after=True, status_ready=True, flood=0):
        self.chunks, self.err_chunks = list(chunks), list(err)
        self.status, self.exec_error, self.status_ready = status, exec_error, status_ready
        self.closed, self.eof_received = False, False
        self._eof_after, self.flood = eof_after, flood
        self.executed = []

    def exec_command(self, command):
        if self.exec_error:
            raise self.exec_error
        self.executed.append(command)

    def recv_ready(self):
        return bool(self.chunks) or self.flood > 0

    def recv(self, n):
        if self.flood > 0:
            size = min(n, self.flood)
            self.flood -= size
            return b"x" * size
        data = self.chunks.pop(0) if self.chunks else b""
        if not self.chunks and self._eof_after:
            self.eof_received = True
        return data

    def recv_stderr_ready(self):
        return bool(self.err_chunks)

    def recv_stderr(self, n):
        return self.err_chunks.pop(0) if self.err_chunks else b""

    def exit_status_ready(self):
        return self.status is not None and self.status_ready and (self.eof_received or not self.chunks)

    def recv_exit_status(self):
        return self.status

    def close(self):
        self.closed = True


class StubSession:
    def __init__(self, chan=None, open_error=None, active=True):
        self.chan, self.open_error, self.active = chan, open_error, active
        self.target = self

    def open_session(self, timeout):
        if self.open_error:
            raise self.open_error
        return self.chan

    def is_active(self):
        return self.active


def cmd(**kw):
    return {"id": "c1", "run": "echo hi", "sensitive": False, "timeout": None, "expect_exit": [0]} | kw


SPEC = {"op_timeout": 5, "max_output": 1000}


def run(command, chan, *, deadline=None, cancel=lambda: False, total=0, spec=SPEC, session=None):
    deadline = deadline if deadline is not None else time.monotonic() + 30
    return _exec_one(session or StubSession(chan), command, spec, deadline, cancel, total)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(ssh_core, "POLL", 0.001)


def test_a_command_that_ends_with_an_expected_status_succeeds_and_keeps_both_streams():
    chan = StubExec(chunks=[b"salut\n"], err=[b"avert\n"], status=0)
    res, abort = run(cmd(), chan)
    assert res["status"] == "ok" and res["exit_status"] == 0 and abort is None
    assert (res["stdout"], res["stderr"], res["command"]) == ("salut\n", "avert\n", "echo hi")
    assert chan.executed == ["echo hi"] and chan.closed is True and res["_size"] == len(b"salut\navert\n")


def test_an_unexpected_exit_status_is_a_failure_with_the_status_in_the_message():
    res, _ = run(cmd(), StubExec(chunks=[b"x"], status=3))
    assert res["status"] == "failed" and res["exit_status"] == 3
    assert res["error"]["type"] == "remote_status" and "3 non attendu" in res["error"]["message"]
    ok, _ = run(cmd(expect_exit=[0, 3]), StubExec(chunks=[b"x"], status=3))
    assert ok["status"] == "ok"


def test_a_sensitive_command_is_masked_and_its_output_is_never_returned():
    res, _ = run(cmd(sensitive=True, run="mysql -pSECRET"), StubExec(chunks=[b"mot de passe"], status=0))
    assert res["command"] == MASKED and res["redacted"] is True and res["stdout"] is None and "SECRET" not in repr(res)


def test_a_command_that_starts_after_the_global_deadline_is_not_run():
    res, abort = run(cmd(), StubExec(), deadline=time.monotonic() - 1)
    assert res["status"] == "not_run" and abort.code == "SSH_TIMEOUT"


def test_a_channel_refused_by_the_server_is_a_connect_failure_that_does_not_abort_the_step():
    res, abort = run(cmd(), None, session=StubSession(open_error=OSError("non")))
    assert (
        res["status"] == "failed"
        and res["error"]["type"] == "connect"
        and "OSError" in res["error"]["message"]
        and abort is None
    )


def test_a_cancelled_step_never_starts_the_command_and_closes_the_channel():
    chan = StubExec()
    res, abort = run(cmd(), chan, cancel=lambda: True)
    assert chan.executed == [] and chan.closed is True and res["status"] == "not_run" and abort.code == "SSH_CANCELLED"


def test_an_exec_request_that_fails_is_uncertain_because_it_may_have_reached_the_server():
    res, abort = run(cmd(), StubExec(exec_error=EOFError("coupé")))
    assert (
        res["status"] == "failed"
        and res["uncertain"] is True
        and res["error"]["type"] == "closed"
        and "avant la confirmation" in res["error"]["message"]
    )
    assert res["error"]["uncertain"] is True and abort is None


def test_output_past_the_limit_truncates_marks_uncertain_and_stops_reading():
    res, _ = run(cmd(), StubExec(flood=100000), spec={"op_timeout": 5, "max_output": 100})
    assert (
        res["status"] == "failed"
        and res["truncated"] is True
        and res["uncertain"] is True
        and res["error"]["type"] == "output_limit"
    )
    assert len(res["stdout"]) == 100


def test_the_total_output_of_a_step_is_bounded_across_commands():
    res, _ = run(cmd(), StubExec(chunks=[b"abc"]), total=MAX_TOTAL - 1)
    assert res["error"]["type"] == "output_limit" and res["truncated"] is True


def test_a_command_cancelled_while_running_is_uncertain_and_aborts_the_step():
    calls = iter([False, False, True])
    res, abort = run(cmd(), StubExec(status=None, eof_after=False), cancel=lambda: next(calls, True))
    assert (
        res["status"] == "failed"
        and res["error"]["type"] == "cancelled"
        and res["uncertain"] is True
        and abort.code == "SSH_CANCELLED"
    )


def test_a_command_that_outlives_its_limit_times_out_and_may_have_continued():
    res, _ = run(cmd(timeout=1), StubExec(status=None, eof_after=False), spec={"op_timeout": 1, "max_output": 1000})
    assert res["status"] == "timeout" and res["timed_out"] is True and res["uncertain"] is True
    assert "Délai de 1 s dépassé" in res["error"]["message"]


def test_a_stream_closed_without_an_exit_status_gets_a_grace_period_then_ends_without_inventing_a_status():
    chan = StubExec(chunks=[b"fin"], status=None)
    chan.status_ready = False
    session = StubSession(chan, active=True)
    res, _ = run(cmd(), chan, session=session)
    # the stream ended cleanly (end of stream received on a live connection): no status is invented, and it is not a failure
    assert res["exit_status"] is None and res["status"] == "ok"


def test_a_connection_lost_without_a_clean_end_is_an_uncertain_closed_failure():
    chan = StubExec(chunks=[b"x"], status=None, eof_after=False)
    chan.closed = True
    res, _ = run(cmd(), chan, session=StubSession(chan, active=False))
    assert res["status"] == "failed" and res["uncertain"] is True and res["error"]["type"] == "closed"
    assert "Connexion coupée" in res["error"]["message"]


def test_a_vanished_channel_reported_as_minus_one_is_not_a_status():
    res, _ = run(cmd(), StubExec(chunks=[b"x"], status=-1), session=StubSession(None, active=False))
    assert res["exit_status"] is None and res["status"] == "failed" and res["error"]["type"] == "closed"
