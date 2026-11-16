"""`shell` sessions of the SSH step: one persistent session for the duration of the step, driven by `send` / `expect` steps.

Nothing here knows a device, a prompt or an error text: every pattern comes from the workflow. Rules (see also docs/USER_GUIDE.md):

- Buffer. Received text is kept in ONE unconsumed buffer. A step that sends text first DISCARDS what was received before the send
  (an old prompt can never satisfy this step's wait); a step that only waits looks at what is already there (the banner, the first
  prompt). A match consumes the buffer up to the end of the match; the rest stays for the next step.
- Priority at every evaluation of the unconsumed buffer: 1. `failure` patterns, 2. `responses` (configured interactions, in declared
  order, each at most `max` times), 3. `expect` patterns. The first category with a match decides; inside a category the declared
  order wins. After an `expect` match, `success` (if given) must match what the step captured, otherwise the step fails.
- A recognised prompt does not prove that a command succeeded and the session has no reliable exit status: `exit_status` is always null.
- With a PTY the server merges stdout and stderr: the result says `streams: "combined"` and `stderr` stays empty. Without a PTY the two
  streams are separate; patterns are matched on stdout only.
- Bounds: pattern length and shape (nested unbounded repeats and back-references are refused), matching window, buffer size,
  number of steps, responses and total interactions, delays."""

import codecs
import importlib
import re
import time

from . import ssh_core as core
from .ssh_core import MASKED, POLL, SshStepError

# The parse tree of `re` is the only way to inspect repeats and back-references (public API since Python 3.11 under a private name).
_PARSER = importlib.import_module("re._parser")
_CONSTANTS = importlib.import_module("re._constants")
MAX_REGEX = 200
MAX_PATTERNS = 10
MAX_RESPONSES = 10
MAX_RESPONSE_REPEAT = 10
MAX_INTERACTIONS = 200
WINDOW = 8192  # patterns are searched in the last 8 KiB of the unconsumed buffer (bounds the cost of a search)
SEARCH_BUDGET = (
    2.0  # seconds: a single search slower than this ends the step (a pattern that costs that much is refused)
)
EOLS = {"lf": "\n", "cr": "\r", "crlf": "\r\n", "none": ""}
TERM_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
PATTERN_KINDS = ("expect", "success", "failure")


def check_regex(pattern, label: str) -> re.Pattern:
    """Compiles a workflow-provided pattern or refuses it: bounded length, no back-reference, no nested unbounded repeat (the usual
    source of catastrophic backtracking). Matching is also limited to a window, so even an accepted pattern has a bounded cost."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX or "\x00" in pattern:
        raise SshStepError("SSH_CONFIG", f"{label} : expression régulière de 1 à {MAX_REGEX} caractères.")
    try:
        compiled = re.compile(pattern)
        tree = _PARSER.parse(pattern)
    except (re.error, RecursionError, OverflowError) as exc:
        raise SshStepError("SSH_CONFIG", f"{label} : expression régulière invalide ({exc}).") from None
    if _risky(tree, inside_repeat=False):
        raise SshStepError(
            "SSH_CONFIG",
            f"{label} : expression régulière refusée (répétition illimitée imbriquée ou référence arrière) : "
            "simplifiez-la ou bornez les répétitions.",
        )
    return compiled


def _unbounded_repeats(tree) -> int:
    n = 0
    for op, arg in tree:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            n += (1 if arg[1] is _CONSTANTS.MAXREPEAT else 0) + _unbounded_repeats(arg[2])
        elif name == "SUBPATTERN":
            n += _unbounded_repeats(arg[-1])
        elif name == "BRANCH":
            n += max((_unbounded_repeats(b) for b in arg[1]), default=0)
        elif name in ("ASSERT", "ASSERT_NOT", "ATOMIC_GROUP"):
            n += _unbounded_repeats(arg[-1])
    return n


def _risky(tree, inside_repeat: bool = False) -> bool:
    """Shapes that make `re` backtrack badly: a back-reference, a repeat inside a repeat (bounded or not), an alternative inside a repeat
    (overlapping branches), or more than one unbounded repeat in a pattern (polynomial cost). One `\\s*` or `.*` stays allowed."""
    if not inside_repeat and _unbounded_repeats(tree) > 1:
        return True
    for op, arg in tree:
        name = str(op)
        if name == "GROUPREF":
            return True
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            low, high, sub = arg
            repeats = high > 1
            if inside_repeat and repeats:
                return True
            if _risky(sub, inside_repeat or repeats):
                return True
        elif name == "SUBPATTERN":
            if _risky(arg[-1], inside_repeat):
                return True
        elif name == "BRANCH":
            if inside_repeat or any(_risky(b, inside_repeat) for b in arg[1]):
                return True
        elif name in ("ASSERT", "ASSERT_NOT", "ATOMIC_GROUP") and _risky(arg[-1], inside_repeat):
            return True
    return False


def clean_pty(raw) -> dict:
    if raw in (None, False, {}):
        return {"enabled": False}
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        raise SshStepError("SSH_CONFIG", "PTY : un objet {enabled, term, cols, rows}.")
    term = raw.get("term", "xterm")
    cols, rows = raw.get("cols", 80), raw.get("rows", 24)
    if not isinstance(term, str) or not TERM_RE.match(term):
        raise SshStepError("SSH_CONFIG", "PTY : type de terminal invalide (lettres, chiffres, . _ - ; 32 au plus).")
    for value, low, high, label in ((cols, 20, 500, "colonnes"), (rows, 5, 200, "lignes")):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise SshStepError("SSH_CONFIG", f"PTY : {label} de {low} à {high}.")
    return {"enabled": raw.get("enabled", True) is not False, "term": term, "cols": cols, "rows": rows}


def _patterns(raw, label: str, resolve, ctx) -> list[str]:
    if raw in (None, "", []):
        return []
    value = resolve(raw, ctx) if isinstance(raw, str) else [resolve(x, ctx) if isinstance(x, str) else x for x in raw]
    value = [value] if isinstance(value, str) else value
    if not isinstance(value, list) or len(value) > MAX_PATTERNS:
        raise SshStepError("SSH_CONFIG", f"{label} : un motif ou une liste de {MAX_PATTERNS} motifs au plus.")
    for p in value:
        check_regex(p, label)
    return list(value)


def _eol(raw, label) -> str:
    if raw in (None, ""):
        return "lf"
    if raw not in EOLS:
        raise SshStepError("SSH_CONFIG", f"{label} : fin de ligne « lf », « cr », « crlf » ou « none ».")
    return raw


def _send_fields(obj, label, aliases, resolve, ctx) -> dict:
    """`send` (text) xor `send_secret` (alias of a declared secret): a secret is referenced, never written in the workflow."""
    _text, MAX_TEXT = core.text_in, core.MAX_TEXT

    secret, send = obj.get("send_secret"), obj.get("send")
    if secret is not None and send is not None:
        raise SshStepError("SSH_CONFIG", f"{label} : « send » ou « send_secret », pas les deux.")
    if secret is not None:
        if not isinstance(secret, str) or secret not in aliases:
            raise SshStepError(
                "SSH_CONFIG", f"{label} : « send_secret » doit nommer un alias déclaré dans « secrets »."
            )
        return {"send": None, "send_secret": secret, "eol": _eol(obj.get("eol"), label)}
    if send is None:
        return {"send": None, "send_secret": None, "eol": "lf"}
    text = resolve(send, ctx)
    return {
        "send": _text(text, f"{label} : texte envoyé", limit=MAX_TEXT, allow_empty=True),
        "send_secret": None,
        "eol": _eol(obj.get("eol"), label),
    }


def _prepare_responses(obj: dict, step: dict, label: str, aliases: dict, resolve, ctx) -> tuple[list[dict], int]:
    """The scripted answers of a step: (answers, number of interactions they can take)."""
    _int = core.int_in
    used = 0
    responses = obj.get("responses") or []
    if not isinstance(responses, list) or len(responses) > MAX_RESPONSES:
        raise SshStepError("SSH_CONFIG", f"{label} : « responses » : {MAX_RESPONSES} interactions au plus.")
    if responses and not step["expect"]:
        raise SshStepError("SSH_CONFIG", f"{label} : « responses » demande aussi « expect » (ce qui termine l'étape).")
    out = []
    for j, r in enumerate(responses, 1):
        if not isinstance(r, dict):
            raise SshStepError("SSH_CONFIG", f"{label}, interaction {j} : un objet {{expect, send}}.")
        rl = f"{label}, interaction {j}"
        pattern = resolve(r.get("expect"), ctx) if isinstance(r.get("expect"), str) else r.get("expect")
        check_regex(pattern, f"{rl} : « expect »")
        fields = _send_fields(r, rl, aliases, resolve, ctx)
        if fields["send"] is None and fields["send_secret"] is None:
            raise SshStepError("SSH_CONFIG", f"{rl} : indiquez « send » ou « send_secret ».")
        repeat = _int(r.get("max"), 1, 1, MAX_RESPONSE_REPEAT, f"{rl} : « max »")
        used += repeat
        out.append(
            {
                "expect": pattern,
                **fields,
                "max": repeat,
                "sensitive": bool(r.get("sensitive")) or fields["send_secret"] is not None,
            }
        )
    return out, used


def prepare_steps(raw, resolve, ctx, aliases: dict) -> list[dict]:
    ID_RE, MAX_OP_TIMEOUT, MAX_STEPS, _int = core.ID_RE, core.MAX_OP_TIMEOUT, core.MAX_STEPS, core.int_in

    items = resolve(raw, ctx) if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items:
        raise SshStepError("SSH_CONFIG", "Indiquez au moins une étape de session.")
    if len(items) > MAX_STEPS:
        raise SshStepError("SSH_CONFIG", f"Trop d'étapes (au plus {MAX_STEPS}).")
    out, ids, interactions = [], set(), 0
    for i, obj in enumerate(items, 1):
        if not isinstance(obj, dict):
            raise SshStepError("SSH_CONFIG", f"Étape {i} : un objet {{id, send, expect, …}}.")
        sid = str(obj.get("id") or f"s{i}")
        label = f"Étape {sid}"
        if not ID_RE.match(sid) or sid in ids:
            raise SshStepError("SSH_CONFIG", f"Étape {i} : identifiant invalide ou en double.")
        ids.add(sid)
        step = {"id": sid, **_send_fields(obj, label, aliases, resolve, ctx)}
        for kind in PATTERN_KINDS:
            step[kind] = _patterns(obj.get(kind), f"{label} : « {kind} »", resolve, ctx)
        if not step["expect"] and step["success"]:
            step["expect"] = list(step["success"])  # a success pattern is also what the step waits for
        if step["send"] is None and step["send_secret"] is None and not step["expect"]:
            raise SshStepError("SSH_CONFIG", f"{label} : indiquez « send » (ou « send_secret ») ou « expect ».")
        step["responses"], used = _prepare_responses(obj, step, label, aliases, resolve, ctx)
        interactions += used
        if interactions > MAX_INTERACTIONS:
            raise SshStepError("SSH_CONFIG", f"Trop d'interactions au total (au plus {MAX_INTERACTIONS}).")
        step["sensitive"] = bool(obj.get("sensitive")) or step["send_secret"] is not None
        step["ignore_error"] = bool(obj.get("ignore_error"))
        step["timeout"] = _int(obj.get("timeout_seconds"), None, 1, MAX_OP_TIMEOUT, f"{label} : délai")
        out.append(step)
    return out


# ---------------------------------------------------------------------------------------------------- running
class _Channel:
    """Reads a session channel into the unconsumed buffer; decodes incrementally (a character may be split between two reads)."""

    def __init__(self, chan, cap: int):
        self.chan, self.cap = chan, cap
        self.pending = ""
        self.err = ""
        self._dec = {
            "out": codecs.getincrementaldecoder("utf-8")("replace"),
            "err": codecs.getincrementaldecoder("utf-8")("replace"),
        }
        self.overflow = False

    def pump(self) -> bool:
        moved = False
        for ready, recv, key in (
            (self.chan.recv_ready, self.chan.recv, "out"),
            (self.chan.recv_stderr_ready, self.chan.recv_stderr, "err"),
        ):
            while ready():
                if len(self.pending) > self.cap or len(self.err) > self.cap:
                    self.overflow = (
                        True  # past the limit: stop reading (bounded memory), the step ends with an output-limit error
                    )
                    return moved
                data = recv(65536)
                if not data:
                    break
                moved = True
                text = self._dec[key].decode(data)
                if key == "out":
                    self.pending += text
                else:
                    self.err += text
        if len(self.pending) > self.cap or len(self.err) > self.cap:
            self.overflow = True
        return moved

    @property
    def finished(self) -> bool:
        return self.chan.closed or (
            self.chan.eof_received and not self.chan.recv_ready() and not self.chan.recv_stderr_ready()
        )


def _search(patterns, text: str):
    """(index, match) of the first pattern, in declared order, that matches the last window of `text`."""
    offset = max(0, len(text) - WINDOW)
    window = text[offset:]
    for i, p in enumerate(patterns):
        t0 = time.monotonic()
        m = re.compile(p).search(window)
        if time.monotonic() - t0 > SEARCH_BUDGET:  # cannot be interrupted, but it is detected and never repeated
            raise SshStepError("SSH_CONFIG", "Un motif est trop coûteux à évaluer sur ce texte : simplifiez-le.")
        if m:
            return i, m, offset
    return None, None, 0


def _send(chan_obj, step_or_resp, alias_values) -> str:
    text = alias_values[step_or_resp["send_secret"]] if step_or_resp.get("send_secret") else step_or_resp["send"]
    chan_obj.chan.send((text + EOLS[step_or_resp["eol"]]).encode())
    if step_or_resp.get("send_secret"):
        return f"<secret:{step_or_resp['send_secret']}>"
    return MASKED if step_or_resp.get("sensitive") else text[:500]


def run_shell(session, spec, alias_values, deadline, cancel_check, started) -> dict:
    results, failed, abort = [], None, None
    pty = spec["pty"]
    try:
        raw_chan = session.target.open_session(timeout=min(10, max(1.0, deadline - time.monotonic())))
        wait = min(10, max(1.0, deadline - time.monotonic()))
        if pty["enabled"]:
            core.bounded(
                raw_chan, wait, lambda: raw_chan.get_pty(term=pty["term"], width=pty["cols"], height=pty["rows"])
            )
        core.bounded(raw_chan, wait, raw_chan.invoke_shell)
    except Exception as exc:  # noqa: BLE001
        err = core.SshStepError("SSH_CONNECT", f"Session interactive refusée par le serveur ({type(exc).__name__}).")
        return core.build_output(
            spec,
            [],
            connected=True,
            simulated=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            failed=None,
            error=err,
            hops=session.hops_info,
        )
    chan = _Channel(raw_chan, spec["max_output"])
    total = 0
    inherit = False  # a sensitive step that does not wait leaves its output (and echo) for the next step that waits: that one is sensitive too
    try:
        for step in spec["steps"]:
            if abort is not None or (failed is not None and spec["stop_on_error"]):
                results.append(
                    {
                        **core.blank_result(step["id"], "step", pty["enabled"]),
                        "status": "not_run",
                        "sensitive": step["sensitive"],
                    }
                )
                continue
            waits = bool(step["expect"])
            if inherit and waits:
                step = {**step, "sensitive": True}
            res, abort = _step(chan, step, spec, alias_values, deadline, cancel_check, pty["enabled"])
            inherit = (inherit and not waits) or (step["sensitive"] and not waits)
            total += res.pop("_size", 0)
            res["ignored"] = step["ignore_error"] and res["status"] in ("failed", "timeout")
            results.append(res)
            if res["status"] in ("failed", "timeout") and not step["ignore_error"]:
                failed = failed or res
            if total > core.MAX_TOTAL:
                abort = abort or core.SshStepError("SSH_OUTPUT_LIMIT", "Limite de sortie totale atteinte.")
    finally:
        try:
            raw_chan.close()  # closing the channel does not guarantee that a remote command stopped
        except Exception:  # noqa: BLE001, S110
            pass
    return core.build_output(
        spec,
        results,
        connected=True,
        simulated=False,
        duration_ms=int((time.monotonic() - started) * 1000),
        failed=failed,
        error=abort,
        hops=session.hops_info,
    )


_AGAIN = object()  # a check made progress (data consumed, an answer sent): look at the channel again without waiting


class _StepRun:
    """One session step: what it sends, what it waits for, which prompts it answers, and how it ends. The state of the wait lives here
    (what was captured, how many times each prompt may still be answered, whether anything was sent) so that each check of the wait
    is a short method that returns an outcome `(status, error kind, message, uncertain)`, `_AGAIN`, or None to keep waiting."""

    def __init__(self, chan, step, spec, alias_values, deadline, cancel_check, combined):
        self.chan, self.step, self.spec, self.alias_values = chan, step, spec, alias_values
        self.cancel_check, self.combined = cancel_check, combined
        self.res = core.blank_result(step["id"], "step")
        self.res.update(
            sensitive=step["sensitive"],
            sent=None,
            matched=None,
            interactions=[],
            streams="combined" if combined else "separate",
        )
        self.t0 = time.monotonic()
        self.limit = min(step["timeout"] or spec["op_timeout"], deadline - self.t0)
        self.end = self.t0 + self.limit
        self.captured: list[str] = []
        self.repeats = [r["max"] for r in step["responses"]]
        self.abort = None
        self.sent_something = False

    def run(self):
        if self.limit <= 0:
            self.res["status"] = "not_run"
            return self.res, core.SshStepError("SSH_TIMEOUT", "Délai global dépassé avant cette étape.")
        refused = self._send_first()
        if refused:
            return refused
        if not self.step["expect"]:  # send-only: no wait, no completion check
            self.res["status"] = "ok"
            self.res["duration_ms"] = self._elapsed_ms()
            core.redact_sensitive(self.res)
            return self.res, None
        return self._conclude(self._wait())

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def _consume(self, offset: int, match) -> None:
        """What the match ended on is captured; the rest stays in the buffer for the next wait."""
        self.captured.append(self.chan.pending[: offset + match.end()])
        self.chan.pending = self.chan.pending[offset + match.end() :]

    def _send_first(self):
        """The text this step sends before waiting. A `(result, error)` pair when the step ends here, else None."""
        step, chan, res = self.step, self.chan, self.res
        if step["send"] is None and not step["send_secret"]:
            return None
        if self.cancel_check():  # the step is no longer ours: nothing is sent
            res["status"] = "not_run"
            return res, SshStepError("SSH_CANCELLED", "Étape annulée.", "")
        chan.pump()
        chan.pending = ""  # what arrived before the send can never satisfy this step's wait
        chan.err = ""
        try:
            res["sent"] = _send(chan, {**step, "sensitive": step["sensitive"]}, self.alias_values)
        except (OSError, EOFError):
            return _close(
                res, "closed", "La session a été fermée par le serveur avant l'envoi.", self.sent_something
            ), None
        self.sent_something = True
        return None

    # ---- the wait: each check returns an outcome, _AGAIN, or None (nothing to report yet)
    def _overflowed(self):
        if self.chan.overflow:
            return ("failed", "output_limit", "Limite de sortie atteinte avant que l'étape ne soit satisfaite.", True)
        return None

    def _failure_seen(self):
        i, m, off = _search(self.step["failure"], self.chan.pending)
        if not m:
            return None
        self._consume(off, m)
        self.res["matched"] = {"kind": "failure", "pattern": self.step["failure"][i]}
        return ("failed", "remote_status", "Motif d'échec reconnu dans la sortie.", False)

    def _prompt_answered(self):
        for j, r in enumerate(self.step["responses"]):
            if self.repeats[j] <= 0:
                continue
            _, rm, roff = _search([r["expect"]], self.chan.pending)
            if not rm:
                continue
            self._consume(roff, rm)
            self.repeats[j] -= 1
            try:
                sent = _send(self.chan, r, self.alias_values)
            except (OSError, EOFError):
                return ("failed", "closed", "La session a été fermée par le serveur pendant une interaction.", True)
            self.res["interactions"].append({"expect": r["expect"], "sent": sent, "after_ms": self._elapsed_ms()})
            self.sent_something = True
            return _AGAIN
        return None

    def _expectation_met(self):
        i, m, off = _search(self.step["expect"], self.chan.pending)
        if not m:
            return None
        self._consume(off, m)
        self.res["matched"] = {"kind": "expect", "pattern": self.step["expect"][i]}
        success = self.step["success"]
        if success and not any(re.search(p, "".join(self.captured)[-WINDOW:]) for p in success):
            return ("failed", "remote_status", "Aucun motif de succès dans la sortie de l'étape.", False)
        return ("ok", None, None, False)

    def _cancelled(self):
        if not self.cancel_check():
            return None
        self.abort = core.SshStepError("SSH_CANCELLED", "Étape annulée.", "")
        return ("failed", "cancelled", "Annulée : la commande distante a pu se poursuivre.", True)

    def _timed_out(self):
        if time.monotonic() < self.end:
            return None
        return ("timeout", "expect", f"Attente non satisfaite après {int(self.limit)} s.", True)

    def _session_closed(self):
        if not self.chan.finished:
            return None
        if self.chan.pump():  # data arrived with the close: evaluate it before concluding
            return _AGAIN
        return ("failed", "closed", "Le serveur a fermé la session avant la fin de l'attente.", True)

    def _wait(self):
        checks = (
            self._overflowed,
            self._failure_seen,
            self._prompt_answered,
            self._expectation_met,
            self._cancelled,
            self._timed_out,
            self._session_closed,
        )
        try:
            while True:
                self.chan.pump()
                for check in checks:
                    verdict = check()
                    if verdict is not None:
                        break
                else:
                    time.sleep(POLL)
                    continue
                if verdict is not _AGAIN:
                    return verdict
        except SshStepError as exc:  # a pattern too costly to evaluate: the step ends, the cost is never repeated
            return ("failed", "config", exc.message, False)

    def _conclude(self, outcome):
        res, chan = self.res, self.chan
        status, kind, message, uncertain = outcome
        res["status"] = status
        if kind:
            res["error"] = core.error_dict(
                kind, message, **({"uncertain": True} if uncertain and self.sent_something else {})
            )
        res["uncertain"] = bool(uncertain and self.sent_something)
        res["timed_out"] = status == "timeout"
        text = "".join(self.captured)
        if status != "ok":
            text += chan.pending  # what arrived and matched nothing: kept (bounded) to understand the failure
        cap = self.spec["max_output"]
        res["truncated"] = len(text) > cap or kind == "output_limit"
        res["stdout"], res["stderr"] = text[:cap], chan.err[:cap]
        res["_size"] = len(res["stdout"]) + len(res["stderr"])
        res["duration_ms"] = self._elapsed_ms()
        if self.combined:
            res["stderr"] = ""
        chan.err = ""
        core.redact_sensitive(res)
        return res, self.abort


def _step(chan, step, spec, alias_values, deadline, cancel_check, combined):
    return _StepRun(chan, step, spec, alias_values, deadline, cancel_check, combined).run()


def _close(res, kind, message, sent) -> dict:
    res["status"], res["error"] = "failed", core.error_dict(kind, message)
    return res
