"""SSH step: regressions for the defects found by the independent review (unbounded reads, regex cost, sensitive text kept in the job spec,
abrupt closes read as success, blocking calls, early cancellation, numeric host spellings)."""

import threading
import time

import pytest

from runs import engine, http_node, ssh_core, ssh_node, ssh_shell
from tests.ssh_helpers import drain, launch, publish, run_ssh, step
from tests.ssh_helpers import ssh_node as ssh
from tests.test_ssh_shell import P, ScriptedChannel, _run_step, shell
from workflows.expressions import resolve

pytest.importorskip("paramiko")


# ---- B1 / B2: reads are bounded, whatever the server floods
def test_exec_flood_ends_quickly_with_bounded_memory(ssh_ws, world, sshd, target):
    started = time.monotonic()
    _, sr = run_ssh(ssh_ws, world, commands=["big 3000000"], max_output_bytes=1000, timeout_seconds=30)
    r = sr.output["results"][0]
    assert r["error"]["type"] == "output_limit" and len(r["stdout"]) == 1000 and r["truncated"]
    assert time.monotonic() - started < 15


def test_shell_flood_ends_with_an_output_limit_error(ssh_ws, world, sshd, target):
    started = time.monotonic()
    _, sr = shell(
        ssh_ws, world, [{"expect": P}, {"id": "g", "send": "big 3000000", "expect": P}], max_output_bytes=1000
    )
    assert sr.output["results"][1]["error"]["type"] == "output_limit" and time.monotonic() - started < 15


def test_pump_stops_reading_once_past_the_limit():
    class Endless(ScriptedChannel):
        def recv_ready(self):
            return True

        def recv(self, n):
            return b"x" * n

    chan = ssh_shell._Channel(Endless(), 1000)
    chan.pump()
    assert chan.overflow and len(chan.pending) <= 1000 + 65536  # one read past the limit at most, never an endless loop


# ---- B3: regex shapes and cost
@pytest.mark.parametrize("pattern", ["(a|aa)+$", "(a{1,1000}){1,1000}$", ".*.*.*.*x", "(.+)+z", "(?:ab|a)*c", "x*y*z+"])
def test_overlapping_nested_or_multiple_unbounded_patterns_are_refused(pattern):
    with pytest.raises(ssh_core.SshStepError):
        ssh_shell.check_regex(pattern, "t")


@pytest.mark.parametrize(
    "pattern", [r"host> $", r"(?i)password:\s*$", r"[#>] ?$", r"(ok|fail)\d{1,3}", r"a{2,5}b+", r"ERROR: .*"]
)
def test_ordinary_patterns_stay_accepted(pattern):
    assert ssh_shell.check_regex(pattern, "t")


def test_a_search_slower_than_the_budget_ends_the_step_as_a_configuration_error(monkeypatch):
    monkeypatch.setattr(ssh_shell, "SEARCH_BUDGET", -1.0)  # any search is « too slow »
    res = _run_step(ScriptedChannel(buffered="hote> "), {"expect": ["hote> $"]})
    assert res["status"] == "failed" and res["error"]["type"] == "config"


# ---- B4 / B5: nothing sensitive stays in the job spec, whichever way the step ends
SENSITIVE = "texte-sensible-4242"


def _sensitive_graph(ws, world):
    nodes = [ssh(commands=[{"id": "t", "run": f"echo {SENSITIVE}", "sensitive": True}])]
    return publish(ws, "wf-spec", nodes, [], user=world["designer"])


def test_a_cancelled_step_keeps_no_sensitive_text(ssh_ws, world, sshd, target):
    run = launch(ssh_ws, _sensitive_graph(ssh_ws, world), world["designer"])
    engine.advance(run.pk)
    claimed = http_node.claim_next()  # a worker took it and is running
    assert SENSITIVE in repr(claimed.http_job["spec"])  # needed while it runs
    engine.request_run_cancel(run.pk, world["designer"])
    drain(run)
    sr = step(run)
    assert sr.status == "cancelled" and SENSITIVE not in repr(sr.http_job)


def test_an_expired_lease_keeps_no_sensitive_text(ssh_ws, world, sshd, target):
    from datetime import timedelta

    from django.utils import timezone

    run = launch(ssh_ws, _sensitive_graph(ssh_ws, world), world["designer"])
    engine.advance(run.pk)
    claimed = http_node.claim_next()
    claimed.http_job = {**claimed.http_job, "claimed_at": (timezone.now() - timedelta(hours=1)).isoformat()}
    claimed.save()
    drain(run)
    assert SENSITIVE not in repr(step(run).http_job)


def test_scrub_hides_a_sensitive_response_of_a_step_that_is_not_sensitive():
    spec = {
        "steps": [
            {
                "id": "a",
                "send": "ok",
                "sensitive": False,
                "responses": [
                    {"expect": "x", "send": SENSITIVE, "sensitive": True},
                    {"expect": "y", "send": "visible", "sensitive": False},
                ],
            }
        ]
    }
    out = ssh_node.scrub_spec(spec)["steps"][0]
    assert (
        out["send"] == "ok" and out["responses"][0]["send"] == "<masqué>" and out["responses"][1]["send"] == "visible"
    )


def test_scrub_keeps_a_secret_reference_and_hides_every_text_of_a_sensitive_step():
    spec = {
        "steps": [
            {
                "id": "a",
                "send": SENSITIVE,
                "sensitive": True,
                "responses": [
                    {"expect": "x", "send": "t", "sensitive": False},
                    {"expect": "y", "send": None, "send_secret": "pw", "sensitive": True},
                ],
            }
        ]
    }
    out = ssh_node.scrub_spec(spec)["steps"][0]
    assert (
        out["send"] == "<masqué>"
        and out["responses"][0]["send"] == "<masqué>"
        and out["responses"][1]["send_secret"] == "pw"
    )


# ---- B6: an abrupt close is not a success
def test_a_connection_dropped_during_a_command_is_a_failure_and_uncertain(ssh_ws, world, sshd, target):
    threading.Timer(1.0, sshd.drop_connections).start()
    _, sr = run_ssh(ssh_ws, world, commands=["hang"], timeout_seconds=20)
    r = sr.output["results"][0]
    assert (
        r["status"] == "failed"
        and r["error"]["type"] == "closed"
        and r["uncertain"] is True
        and r["exit_status"] is None
    )
    assert sr.error["code"] == "SSH_CLOSED"


# ---- B7: blocking calls, cancellation before anything starts
def test_a_request_that_is_never_answered_is_unblocked_by_closing_the_channel():
    class Hung:
        def __init__(self):
            self.event = threading.Event()

        def close(self):
            self.event.set()

    chan = Hung()
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        ssh_core.bounded(chan, 0.3, lambda: chan.event.wait(10) and (_ for _ in ()).throw(RuntimeError("canal fermé")))
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("fields", [{"commands": ["echo x"]}, {"session": "shell", "steps": [{"expect": P}]}])
def test_a_step_that_is_no_longer_ours_opens_nothing_and_runs_nothing(ssh_ws, world, sshd, target, fields):
    spec = ssh_node.prepare(ssh(**fields), resolve, {}, ssh_ws.pk, None)
    out, err = ssh_node.execute_spec(spec, ssh_ws, None, None, cancel_check=lambda: True)
    assert err["code"] == "SSH_CANCELLED" and sshd.connections == [] and sshd.exec_log == []


# ---- S1: the echo of a sensitive text is masked
def test_the_pty_echo_of_a_sensitive_text_is_masked_in_the_next_step(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "s", "send": f"echo {SENSITIVE}", "sensitive": True}, {"id": "w", "expect": P}]
    run, sr = shell(ssh_ws, world, steps, pty={"enabled": True})
    assert run.status == "succeeded" and SENSITIVE not in repr(sr.output)
    w = next(r for r in sr.output["results"] if r["id"] == "w")
    assert (
        w["redacted"] is True and w["stdout"] is None
    )  # the step that collects the sensitive command's output is sensitive too


# ---- S3 / S4
@pytest.mark.parametrize(
    "host", ["2852039166", "0xa9fea9fe", "0251.0376.0251.0376", "169.254.169.254", "metadata", "a b", "x/y"]
)
def test_numeric_spellings_and_metadata_hosts_are_refused(host):
    assert ssh_core.valid_host(host) is False


@pytest.mark.parametrize("host", ["bastion.exemple.org", "10.1.2.3", "srv-1"])
def test_ordinary_hosts_are_accepted(host):
    assert ssh_core.valid_host(host) is True


@pytest.mark.parametrize("config", [{"port": "²"}, {"connect_timeout_seconds": "²"}, {"port": "٣"}])
def test_non_ascii_digits_are_a_validation_error_not_a_crash(config):
    base = {"host": "h.example", "host_key_sha256": "SHA256:" + "A" * 43, **config}
    with pytest.raises(ssh_core.SshStepError):
        ssh_node.check_connection_config(base)


def test_the_audit_records_which_secrets_a_step_could_read(ssh_ws, world, sshd, target):
    from audit.models import AuditEvent
    from tests.ssh_helpers import make_secret

    make_secret(ssh_ws, "mdp-audit", "valeur-audit-1111")
    run_ssh(ssh_ws, world, session="shell", steps=[{"expect": P}], secrets={"pw": "mdp-audit"})
    ev = AuditEvent.objects.filter(action="ssh.session").latest("id")
    assert ev.data["secrets_used"] == ["mdp-audit"] and "valeur-audit-1111" not in repr(ev.data)


# ---- cancellation keeps what was collected (controlled cancel); a killed process loses it
def test_a_controlled_cancellation_keeps_the_authorized_partial_results(ssh_ws, world, sshd, target, monkeypatch):
    nodes = [
        ssh(
            commands=["echo avant", {"id": "t", "run": f"echo {SENSITIVE}", "sensitive": True}, "hang"],
            stop_on_error=False,
            timeout_seconds=60,
        )
    ]
    v = publish(ssh_ws, "wf-cancel", nodes, [], user=world["designer"])
    run = launch(ssh_ws, v, world["designer"])
    engine.advance(run.pk)
    state = {"cancelled": False}

    def checker(sr):
        def check():
            if not state["cancelled"] and any(
                c == "hang" for _, c in sshd.exec_log
            ):  # the third command is running remotely
                state["cancelled"] = True
                engine.request_run_cancel(run.pk, world["designer"])
                engine.advance(run.pk)  # the engine ends the step as CANCELLED while the worker is alive
            return state["cancelled"]

        return check

    monkeypatch.setattr(ssh_core, "step_checker", checker)
    http_node.process_one()
    sr = step(run)
    assert sr.status == "cancelled"
    results = {r["id"]: r for r in sr.output["results"]}
    assert (
        results["c1"]["stdout"] == "avant\n" and results["c1"]["status"] == "ok"
    )  # collected before the cancellation: kept
    assert results["t"]["redacted"] and results["t"]["stdout"] is None  # sensitive: still hidden
    assert (
        results["c3"]["error"]["type"] == "cancelled"
        and results["c3"]["uncertain"] is True
        and sr.output["error_type"] == "cancelled"
    )
    assert SENSITIVE not in repr(sr.output) + repr(sr.http_job)


def test_a_killed_worker_loses_the_partial_results_and_the_step_is_reported_interrupted(ssh_ws, world, sshd, target):
    from datetime import timedelta

    from django.utils import timezone

    v = publish(ssh_ws, "wf-kill", [ssh(commands=["echo x"])], [], user=world["designer"])
    run = launch(ssh_ws, v, world["designer"])
    engine.advance(run.pk)
    claimed = http_node.claim_next()  # a worker takes it, then is killed: nothing is ever written back
    claimed.http_job = {**claimed.http_job, "claimed_at": (timezone.now() - timedelta(hours=1)).isoformat()}
    claimed.save()
    drain(run)
    sr = step(run)
    assert (
        sr.status == "failed" and sr.error["code"] == "SSH_INTERRUPTED" and not sr.output
    )  # honest: nothing to restore


# ---- the late write of partial results is bound to the right attempt and never changes a terminal status
def _cancelled_with_a_late_worker(ssh_ws, world, slug):
    """A step claimed by a worker, then ended CANCELLED by the engine while that worker is still running. Returns (run, step, the worker's view of it)."""
    v = publish(ssh_ws, slug, [ssh(commands=["echo x"])], [], user=world["designer"])
    run = launch(ssh_ws, v, world["designer"])
    engine.advance(run.pk)
    claimed = http_node.claim_next()
    engine.request_run_cancel(run.pk, world["designer"])
    engine.advance(run.pk)
    return run, step(run), claimed


def _late_write(claimed, result):
    """The real late write (`http_node.keep_partial_results`), with the worker's own (possibly stale) view of the step."""
    return http_node.keep_partial_results(claimed, result)


def test_every_claim_has_its_own_identity_which_survives_the_lease_renewal(ssh_ws, world, sshd, target):
    run, sr, claimed = _cancelled_with_a_late_worker(ssh_ws, world, "wf-claim")
    assert claimed.http_job["claim"] and len(claimed.http_job["claim"]) == 32
    sr.refresh_from_db()
    assert sr.http_job["claim"] == claimed.http_job["claim"]  # kept by the engine's own writes (scrubbing, finishing)


def test_a_late_write_from_another_claim_changes_nothing(ssh_ws, world, sshd, target):
    run, sr, claimed = _cancelled_with_a_late_worker(ssh_ws, world, "wf-other-claim")
    stale = claimed
    stale.http_job = {**claimed.http_job, "claim": "0" * 32}  # a different attempt's identity
    assert _late_write(stale, {"results": ["autre tentative"]}) == 0
    sr.refresh_from_db()
    assert not sr.output and sr.status == "cancelled"


def test_a_late_write_never_overwrites_an_existing_output_nor_changes_status_or_error(ssh_ws, world, sshd, target):
    run, sr, claimed = _cancelled_with_a_late_worker(ssh_ws, world, "wf-twice")
    before_status, before_error, before_finished = sr.status, sr.error, sr.finished_at
    assert _late_write(claimed, {"results": ["premier"]}) == 1
    assert _late_write(claimed, {"results": ["second"]}) == 0  # a second late write finds the output already there
    sr.refresh_from_db()
    assert sr.output == {"results": ["premier"]}
    assert (sr.status, sr.error, sr.finished_at) == (
        before_status,
        before_error,
        before_finished,
    )  # terminal state untouched


@pytest.mark.parametrize("ended_as", ["failed", "succeeded"])
def test_a_late_write_does_not_touch_a_step_that_ended_otherwise(ssh_ws, world, sshd, target, ended_as):
    from runs.models import StepRun

    v = publish(ssh_ws, f"wf-{ended_as}", [ssh(commands=["echo x"])], [], user=world["designer"])
    run = launch(ssh_ws, v, world["designer"])
    engine.advance(run.pk)
    claimed = http_node.claim_next()
    StepRun.objects.filter(pk=claimed.pk).update(
        status=ended_as, output={"results": ["issue réelle"]} if ended_as == "succeeded" else None
    )
    assert _late_write(claimed, {"results": ["tardif"]}) == 0
    assert step(run).status == ended_as and "tardif" not in repr(step(run).output)


def test_a_late_write_for_one_run_never_reaches_another_run_of_the_same_workflow(ssh_ws, world, sshd, target):
    from runs.models import StepRun

    run1, sr1, claimed1 = _cancelled_with_a_late_worker(ssh_ws, world, "wf-same")
    v = sr1.run.workflow_version
    run2 = launch(ssh_ws, v, world["designer"])
    engine.advance(run2.pk)
    claimed2 = http_node.claim_next()
    engine.request_run_cancel(run2.pk, world["designer"])
    engine.advance(run2.pk)
    assert _late_write(claimed1, {"results": ["run 1"]}) == 1
    sr2 = StepRun.objects.get(pk=claimed2.pk)
    assert not sr2.output and sr2.status == "cancelled"  # the other run's step (same node id) is untouched
    assert step(run1).output == {"results": ["run 1"]}
