"""SSH step, stage 3: persistent `shell` sessions (PTY, send / expect / responses, buffer rules, sensitive data, bounds, simulation)."""

import threading
import time

import pytest

from runs import ssh_node
from runs.ssh_shell import check_regex
from tests.ssh_fake import FakeSshd
from tests.ssh_helpers import PASSWORD, make_conn, make_secret, run_graph, run_ssh, schema, step
from tests.ssh_helpers import ssh_node as ssh
from workflows.expressions import resolve

pytest.importorskip("paramiko")
P = "host> "


def shell(ws, world, steps, **fields):
    return run_ssh(ws, world, session="shell", steps=steps, **fields)


def results(sr):
    return {r["id"]: r for r in sr.output["results"]}


# ------------------------------------------------------------------ basics
def test_shell_sends_and_waits_in_one_persistent_session(ssh_ws, world, sshd, target):
    run, sr = shell(
        ssh_ws,
        world,
        [
            {"id": "banniere", "expect": P},
            {"id": "a", "send": "echo bonjour", "expect": P},
            {"id": "b", "send": "echo suite", "expect": P},
        ],
    )
    r = results(sr)
    assert run.status == "succeeded" and sr.output["session"] == "shell" and sr.output["connected"] is True
    assert "Bienvenue" in r["banniere"]["stdout"] and r["banniere"]["stdout"].endswith(P)
    assert "bonjour" in r["a"]["stdout"] and "suite" not in r["a"]["stdout"] and r["a"]["stdout"].endswith(P)
    assert "suite" in r["b"]["stdout"] and r["a"]["sent"] == "echo bonjour"
    assert all(
        x["exit_status"] is None for x in sr.output["results"]
    )  # a prompt proves nothing about success: no invented code
    assert len(sshd.connections) == 1 and [c for _, c in sshd.exec_log] == ["shell:echo bonjour", "shell:echo suite"]


def test_without_a_pty_the_streams_stay_separate_and_with_a_pty_they_are_declared_combined(ssh_ws, world, sshd, target):
    _, sr = shell(ssh_ws, world, [{"expect": P}, {"send": "err oups", "expect": P}])
    r = sr.output["results"][1]
    assert r["streams"] == "separate" and r["stderr"] == "oups\n" and "oups" not in r["stdout"]
    assert sshd.connections[0]["pty"] is None
    run, sr = run_ssh(
        ssh_ws,
        world,
        session="shell",
        steps=[{"expect": P}, {"send": "echo vu", "expect": P}],
        pty={"term": "vt100", "cols": 120, "rows": 40},
    )
    r = sr.output["results"][1]
    assert sshd.connections[-1]["pty"] == {"term": "vt100", "cols": 120, "rows": 40}
    assert (
        r["streams"] == "combined" and r["stderr"] == "" and "echo vu" in r["stdout"] and "vu\r\n" in r["stdout"]
    )  # the echo is part of the stream


def test_a_prompt_that_arrives_in_pieces_is_matched_only_when_complete(ssh_ws, world):
    with FakeSshd(users={"robot": {"password": PASSWORD, "keys": []}}, prompt=("ho", "st", "> "), slow_banner=0.2) as d:
        make_conn(ssh_ws, "frag", d, secret=make_secret(ssh_ws, "pw", PASSWORD))
        _, sr = run_ssh(
            ssh_ws,
            world,
            connection="frag",
            session="shell",
            steps=[{"expect": "host> $"}, {"send": "echo ok", "expect": "host> $"}],
        )
        assert [r["status"] for r in sr.output["results"]] == ["ok", "ok"] and "ok" in sr.output["results"][1]["stdout"]


class ScriptedChannel:
    """A channel double: data already received, and what the server answers once something is sent (no timing involved)."""

    closed = False
    eof_received = False

    def __init__(self, buffered="", reply=""):
        self.buf, self.reply, self.sent = buffered.encode(), reply.encode(), []

    def recv_ready(self):
        return bool(self.buf)

    def recv(self, n):
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, n):
        return b""

    def send(self, data):
        self.sent.append(data)
        self.buf += self.reply


def _run_step(chan, step, **spec_over):
    from runs import ssh_shell

    spec = {"op_timeout": 2, "max_output": 100000, "stop_on_error": True, **spec_over}
    full = {
        "id": "s",
        "send": None,
        "send_secret": None,
        "eol": "lf",
        "expect": [],
        "success": [],
        "failure": [],
        "responses": [],
        "sensitive": False,
        "ignore_error": False,
        "timeout": None,
        **step,
    }
    return ssh_shell._step(
        ssh_shell._Channel(chan, 100000), full, spec, {}, time.monotonic() + 5, lambda: False, False
    )[0]


def test_an_old_prompt_in_the_buffer_cannot_satisfy_the_wait_of_a_step_that_sends():
    chan = ScriptedChannel(buffered="vieux> ", reply="x\nnouveau> ")
    res = _run_step(chan, {"send": "x", "expect": ["> $"]})
    assert (
        res["status"] == "ok" and res["stdout"] == "x\nnouveau> "
    )  # the old prompt was discarded at the send, not matched
    assert chan.sent == [b"x\n"]


def test_a_step_that_only_waits_does_look_at_what_is_already_there():
    res = _run_step(ScriptedChannel(buffered="Bienvenue\nhote> "), {"expect": ["hote> $"]})
    assert res["status"] == "ok" and res["stdout"] == "Bienvenue\nhote> "


def test_a_send_only_step_does_not_wait_and_claims_nothing(ssh_ws, world, sshd, target):
    _, sr = shell(ssh_ws, world, [{"expect": P}, {"id": "a", "send": "echo AAA"}, {"id": "w", "expect": "AAA"}])
    assert (
        results(sr)["a"]["status"] == "ok" and results(sr)["a"]["stdout"] is None and results(sr)["w"]["status"] == "ok"
    )


def test_a_match_consumes_the_buffer_up_to_the_match_and_the_rest_stays_for_the_next_step(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "late", "send": "late", "expect": P}, {"id": "apres", "expect": "tock"}]
    _, sr = shell(ssh_ws, world, steps)
    r = results(sr)
    assert "tick" in r["late"]["stdout"] and "tock" not in r["late"]["stdout"]
    assert r["apres"]["status"] == "ok" and r["apres"]["stdout"].endswith("tock")  # the late text stayed in the buffer


def test_failure_patterns_have_priority_over_expect(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "f", "send": "fail", "expect": P, "failure": ["ERROR: .*"]}, {"send": "echo non"}]
    run, sr = shell(ssh_ws, world, steps)
    f = results(sr)["f"]
    assert f["status"] == "failed" and f["error"]["type"] == "remote_status" and f["matched"]["kind"] == "failure"
    assert "boom" in f["stdout"] and sr.output["failed_step"] == "f" and run.status == "failed"
    assert results(sr)["s3"]["status"] == "not_run" and "shell:echo non" not in [c for _, c in sshd.exec_log]


def test_success_patterns_judge_what_the_step_captured(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "a", "send": "echo fini", "expect": P, "success": ["fini"]}]
    run, sr = shell(ssh_ws, world, steps)
    assert run.status == "succeeded"
    steps[1]["success"] = ["introuvable"]
    run, sr = shell(ssh_ws, world, steps)
    assert results(sr)["a"]["status"] == "failed" and results(sr)["a"]["error"]["type"] == "remote_status"


def test_configured_responses_answer_interactions_in_order(ssh_ws, world, sshd, target):
    steps = [
        {"expect": P},
        {"id": "c", "send": "confirm", "expect": P, "responses": [{"expect": r"\[y/N\] ", "send": "y"}]},
    ]
    _, sr = shell(ssh_ws, world, steps)
    c = results(sr)["c"]
    assert "done" in c["stdout"] and [i["sent"] for i in c["interactions"]] == ["y"] and c["status"] == "ok"
    steps[1]["responses"] = [{"expect": r"\[y/N\] ", "send": "n"}]
    _, sr = shell(ssh_ws, world, steps)
    assert "aborted" in results(sr)["c"]["stdout"]


def test_an_unanswered_interaction_times_out_as_an_unsatisfied_wait(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "c", "send": "confirm", "expect": P, "timeout_seconds": 1}]
    started = time.monotonic()
    _, sr = shell(ssh_ws, world, steps)
    c = results(sr)["c"]
    assert c["status"] == "timeout" and c["error"]["type"] == "expect" and c["uncertain"] and "Sure?" in c["stdout"]
    assert sr.output["error_type"] == "expect" and time.monotonic() - started < 15


def test_eol_can_be_changed(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"send": "echo cr", "eol": "cr", "expect": P}]
    run, sr = shell(ssh_ws, world, steps)
    assert run.status == "succeeded" and "cr" in sr.output["results"][1]["stdout"]


# ------------------------------------------------------------------ secrets and sensitive data
def test_an_interaction_sends_a_referenced_secret_without_exposing_it(ssh_ws, world, sshd, target):
    make_secret(ssh_ws, "mdp-shell", "s3cret-shell")
    steps = [
        {"expect": P},
        {"id": "p", "send": "passwd", "expect": P, "responses": [{"expect": "Password: ", "send_secret": "pw"}]},
        {"id": "apres", "send": "echo suite", "expect": P},
    ]
    run, sr = shell(ssh_ws, world, steps, secrets={"pw": "mdp-shell"}, pty={"enabled": True})
    p = results(sr)["p"]
    assert ("robot", "passwd-result:ok") in sshd.exec_log  # the server received the right value
    assert p["status"] == "ok" and not p["redacted"] and "Password:" in p["stdout"]  # the prompt text is not secret
    assert [i["sent"] for i in p["interactions"]] == ["<secret:pw>"]  # a reference, never the value
    dump = repr(sr.output) + repr(sr.error) + repr(sr.http_job)
    assert "s3cret-shell" not in dump and "suite" in results(sr)["apres"]["stdout"]


def test_a_step_that_sends_a_secret_is_sensitive_as_a_whole(ssh_ws, world, sshd, target):
    make_secret(ssh_ws, "mdp-shell", "s3cret-shell")
    _, sr = shell(
        ssh_ws,
        world,
        [{"expect": P}, {"id": "s", "send_secret": "pw", "expect": P, "timeout_seconds": 1}],
        secrets={"pw": "mdp-shell"},
    )
    s = results(sr)["s"]
    assert s["sensitive"] and s["redacted"] and s["sent"] is None and s["stdout"] is None
    assert "s3cret-shell" not in repr(sr.output)


def test_a_secret_echoed_back_by_the_server_is_masked_in_the_other_results(ssh_ws, world, sshd, target):
    make_secret(ssh_ws, "mdp-shell", "s3cret-shell")
    _, sr = shell(
        ssh_ws,
        world,
        [{"expect": P}, {"id": "e", "send": "echo s3cret-shell", "expect": P}],
        secrets={"pw": "mdp-shell"},
    )
    assert "s3cret-shell" not in repr(sr.output) and "***" in results(sr)["e"]["stdout"]


def test_secret_references_are_aliases_of_declared_secrets_of_the_workspace(ssh_ws, world, sshd, target):
    from workflows.validation import validate

    graph = {
        "inputs": schema(),
        "nodes": [ssh(session="shell", steps=[{"send_secret": "inconnu", "expect": P}])],
        "edges": [],
        "outputs": {},
    }
    assert any(
        "alias déclaré" in i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error"
    )  # refused at publication
    _, sr = run_ssh(
        ssh_ws, world, session="shell", steps=[{"send_secret": "pw", "expect": P}], secrets={"pw": "secret-absent"}
    )
    assert (
        sr.error["code"] == "SSH_CONFIG"
    )  # a declared alias whose secret does not exist: refused when the step starts
    make_secret(world["ws_b"], "autre-espace", "valeur-autre-espace")
    _, sr = run_ssh(
        ssh_ws, world, session="shell", steps=[{"send_secret": "pw", "expect": P}], secrets={"pw": "autre-espace"}
    )
    assert sr.error["code"] == "SSH_CONFIG"  # a secret of another workspace does not exist here
    assert "valeur-autre-espace" not in repr(sr.error) + repr(sr.output)


def test_a_sensitive_step_hides_what_it_sent_received_and_matched(ssh_ws, world, sshd, target):
    steps = [{"expect": P}, {"id": "s", "send": "echo jeton-777", "expect": P, "sensitive": True}]
    run, sr = shell(ssh_ws, world, steps)
    s = results(sr)["s"]
    assert s["redacted"] and s["stdout"] is None and s["stderr"] is None and s["sent"] is None and s["matched"] is None
    assert s["status"] == "ok" and "jeton-777" not in repr(sr.output) + repr(sr.http_job)


# ------------------------------------------------------------------ limits and failures
def test_an_unsatisfied_wait_is_its_own_error_and_keeps_what_arrived(ssh_ws, world, sshd, target):
    _, sr = shell(
        ssh_ws, world, [{"expect": P}, {"id": "w", "send": "echo debut", "expect": "jamais-vu", "timeout_seconds": 1}]
    )
    w = results(sr)["w"]
    assert (
        w["status"] == "timeout"
        and w["error"]["type"] == "expect"
        and "debut" in w["stdout"]
        and w["uncertain"] is True
    )
    assert sr.error["code"] == "SSH_EXPECT"


def test_output_larger_than_the_limit_is_an_error_and_a_big_one_within_the_limit_is_kept(ssh_ws, world, sshd, target):
    _, sr = shell(ssh_ws, world, [{"expect": P}, {"id": "g", "send": "big 200000", "expect": P}], max_output_bytes=1000)
    g = results(sr)["g"]
    assert g["error"]["type"] == "output_limit" and g["truncated"] and len(g["stdout"]) <= 1000
    run, sr = run_ssh(
        ssh_ws,
        world,
        session="shell",
        steps=[{"expect": P}, {"id": "g", "send": "big 100000", "expect": P}],
        max_output_bytes=400000,
    )
    g = results(sr)["g"]
    assert run.status == "succeeded" and len(g["stdout"]) >= 100000 and g["truncated"] is False


def test_a_session_closed_by_the_server_is_reported_and_uncertain(ssh_ws, world, sshd, target):
    _, sr = shell(ssh_ws, world, [{"expect": P}, {"id": "q", "send": "quit", "expect": P, "timeout_seconds": 5}])
    q = results(sr)["q"]
    assert q["status"] == "failed" and q["error"]["type"] == "closed" and q["uncertain"] is True


def test_cancel_interrupts_a_wait_and_marks_it_uncertain(ssh_ws, world, sshd, target):
    spec = ssh_node.prepare(
        ssh(
            session="shell",
            steps=[{"expect": P}, {"send": "slow 30", "expect": "jamais", "timeout_seconds": 60}],
            timeout_seconds=120,
        ),
        resolve,
        {},
        ssh_ws.pk,
        None,
    )
    flag = threading.Event()
    threading.Timer(1.0, flag.set).start()
    started = time.monotonic()
    out, err = ssh_node.execute_spec(spec, ssh_ws, None, None, cancel_check=flag.is_set)
    assert time.monotonic() - started < 10 and err["code"] == "SSH_CANCELLED"
    assert out["results"][1]["error"]["type"] == "cancelled" and out["results"][1]["uncertain"] is True


def test_stop_on_error_false_runs_the_remaining_steps(ssh_ws, world, sshd, target):
    steps = [
        {"expect": P},
        {"id": "f", "send": "fail", "expect": P, "failure": ["ERROR"]},
        {"id": "ok", "send": "echo apres", "expect": P},
    ]
    run, sr = shell(ssh_ws, world, steps, stop_on_error=False)
    assert results(sr)["ok"]["status"] == "ok" and sr.output["status"] == "failed" and sr.output["failed_count"] == 1
    steps[1]["ignore_error"] = True
    run, sr = shell(ssh_ws, world, steps, stop_on_error=False)
    assert run.status == "succeeded"


def test_the_error_branch_reads_partial_shell_results(ssh_ws, world, sshd, target):
    from tests.ssh_helpers import node

    nodes = [
        ssh(session="shell", steps=[{"expect": P}, {"id": "f", "send": "fail", "expect": P, "failure": ["ERROR"]}]),
        node("h", action="echo", inputs={"value": "{{ steps.s.output.results[0].stdout }}"}),
    ]
    run = run_graph(
        ssh_ws, world, nodes, [{"from": "s", "to": "h", "label": "error"}], outputs={"b": "{{ steps.h.output.value }}"}
    )
    assert run.status == "succeeded" and "Bienvenue" in run.outputs["b"]


# ------------------------------------------------------------------ patterns and bounds
@pytest.mark.parametrize("pattern", ["(a+)+$", "(.*)*x", "(a|b+)*c{1,5000}", r"(a)\1", "x" * 201, "(", ""])
def test_dangerous_or_invalid_patterns_are_refused(pattern):
    from runs.ssh_core import SshStepError

    with pytest.raises(SshStepError):
        check_regex(pattern, "test")


@pytest.mark.parametrize(
    "pattern", [r"host> $", r"\$ $", r"(?i)password:\s*$", r"[#>] ?$", r"(ok|fail)\d{1,3}", r"a{2,5}b+"]
)
def test_ordinary_patterns_are_accepted(pattern):
    assert check_regex(pattern, "test")


def test_a_pattern_produced_by_an_expression_is_checked_after_evaluation(ssh_ws, world, target):
    inputs = schema(["p"], p={"type": "string"})
    run = run_graph(
        ssh_ws,
        world,
        [ssh(session="shell", steps=[{"expect": "{{ inputs.p }}"}])],
        inputs=inputs,
        run_inputs={"p": "(a+)+$"},
    )
    assert run.status == "failed" and step(run).error["code"] == "SSH_CONFIG"


@pytest.mark.parametrize(
    "fields, needle",
    [
        ({"steps": []}, "au moins une étape"),
        ({"steps": [{"send": "x", "expect": "a(", "id": "z"}]}, "invalide"),
        ({"steps": [{"id": "a"}]}, "send"),
        ({"steps": [{"id": "a", "send": "x"}, {"id": "a", "send": "y"}]}, "identifiant"),
        ({"steps": [{"send": "x", "send_secret": "pw"}], "secrets": {"pw": "s"}}, "pas les deux"),
        ({"steps": [{"send": "x", "responses": [{"expect": "a", "send": "b"}]}]}, "expect"),
        ({"steps": [{"send": "x", "expect": "a", "responses": [{"expect": "a", "send": "b"}] * 11}]}, "interactions"),
        ({"steps": [{"send": "x"}] * 101}, "Trop d'étapes"),
        (
            {"steps": [{"send": "x", "expect": "a", "responses": [{"expect": "a", "send": "b", "max": 10}] * 10}] * 3},
            "Trop d'interactions",
        ),
        ({"steps": [{"send": "x"}], "pty": {"cols": 5}}, "colonnes"),
        ({"steps": [{"send": "x"}], "pty": {"term": "x y"}}, "terminal"),
        ({"steps": [{"send": "x", "eol": "bizarre"}]}, "fin de ligne"),
        ({"steps": [{"send": "x", "timeout_seconds": 9999}]}, "délai"),
        ({"steps": [{"send": "x"}], "mocks": [{"id": "fantome"}]}, "inconnue"),
    ],
)
def test_static_validation_refuses_bad_shell_configurations(ssh_ws, target, fields, needle):
    from workflows.validation import validate

    graph = {"inputs": schema(), "nodes": [ssh(session="shell", **fields)], "edges": [], "outputs": {}}
    messages = " ".join(i["message"] for i in validate(graph, ssh_ws, "w") if i["level"] == "error")
    assert needle in messages, messages


# ------------------------------------------------------------------ simulated mode
def test_a_simulated_shell_opens_nothing_and_returns_the_mocks(ssh_ws, world, sshd, target, monkeypatch):
    import socket

    from vault.models import Secret

    def boom(*a, **k):
        raise AssertionError("réseau ou secret touché en mode test")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(Secret, "reveal", boom)
    steps = [{"id": "a", "expect": P}, {"id": "b", "send": "echo x", "expect": P}, {"id": "c", "send_secret": "pw"}]
    run, sr = shell(
        ssh_ws, world, steps, mode="test", secrets={"pw": "n-importe"}, mocks=[{"id": "b", "stdout": "x\nhost> "}]
    )
    out = sr.output
    assert run.status == "succeeded" and out["simulated"] is True and out["connected"] is False
    assert (
        results(sr)["b"]["stdout"] == "x\nhost> " and results(sr)["b"]["simulated"] is True and sshd.connections == []
    )
    assert results(sr)["c"]["sent"] is None  # nothing about the secret


def test_a_non_default_session_keeps_the_exec_defaults_intact(ssh_ws, world, sshd, target):
    run, sr = run_ssh(ssh_ws, world, commands=["echo defaut"])
    assert sr.output["session"] == "exec" and run.status == "succeeded"
