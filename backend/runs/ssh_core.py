"""Plumbing of the SSH step: connection chain (direct or through jump hosts), results, limits, masking, `exec` sessions.

See `ssh_node.py` for the contract and the safety rules; `ssh_shell.py` for the interactive session."""

import base64
import hashlib
import io
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field

from core import netsec

log = logging.getLogger("fluxonata.ssh")
MASKED = "<masqué>"
TRANSIENT = {"connect", "timeout"}  # the only errors retried, and only while no command has been sent
DNS_MESSAGE = "Nom d'hôte introuvable."  # what core.netsec raises for a name that does not resolve
POLL = 0.02
MAX_TOTAL = 1048576  # all results of one step, bytes
MAX_STEPS = 100
MAX_TEXT = 8192  # one command or one text sent
MAX_OP_TIMEOUT = 300  # one command or one wait, seconds
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SshStepError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape.", **details):
        super().__init__(message)
        self.code, self.message, self.next_step, self.details = code, message, next_step, details

    @property
    def error_type(self) -> str:
        return self.code.removeprefix("SSH_").lower()


def error_dict(kind: str, message: str, **details) -> dict:
    return {"type": kind, "code": f"SSH_{kind.upper()}", "message": message, **details}


# ------------------------------------------------------------------------------------------------ small validators
def int_in(raw, default, low, high, label):
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not low <= raw <= high:
        raise SshStepError("SSH_CONFIG", f"{label} : un entier de {low} à {high}.")
    return raw


def text_in(raw, label, *, limit=MAX_TEXT, allow_empty=False) -> str:
    value = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    if "\x00" in value or len(value.encode()) > limit or (not value.strip() and not allow_empty):
        raise SshStepError("SSH_CONFIG", f"{label} : texte non vide de {limit} octets au plus, sans caractère nul.")
    return value


def valid_host(host: str) -> bool:
    host = host.strip().lower()
    if not host or len(host) > 253 or any(c in host for c in " /\\@?#,;*[]"):
        return False
    try:
        return not netsec.never_allowed(str(ipaddress.ip_address(host)))
    except ValueError:
        last = host.rsplit(".", 1)[-1]
        if re.fullmatch(
            r"[0-9]+|0x[0-9a-f]+", last
        ):  # 2852039166, 0xa9fea9fe, 0251.0376… : numeric spellings of an address
            return False
        return bool(netsec.HOST_RE.match(host)) and host not in ("metadata.google.internal", "metadata")


def pinned_fingerprints(config: dict) -> list[str]:
    return [f for f in re.split(r"[\s,]+", str(config.get("host_key_sha256") or "").strip()) if f]


def fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def clean_secret_aliases(raw) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict) or len(raw) > 10:
        raise SshStepError("SSH_CONFIG", "Secrets : {alias: nom du secret}, 10 au plus.")
    out = {}
    for alias, name in raw.items():
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", str(alias)) or not isinstance(name, str) or not name or "{{" in name:
            raise SshStepError("SSH_CONFIG", "Secrets : alias simple et nom de secret fixe (sans expression).")
        out[str(alias)] = name
    return out


def clean_mocks(raw) -> dict:
    if raw in (None, [], {}):
        return {}
    rows = [{"id": k, **v} for k, v in raw.items() if isinstance(v, dict)] if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or len(rows) > 100:
        raise SshStepError("SSH_CONFIG", "Simulations : une liste de {id, stdout, stderr, exit_status} (100 au plus).")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise SshStepError(
                "SSH_CONFIG", "Simulation : chaque entrée a un « id » (celui de la commande ou de l'étape)."
            )
        for key in ("stdout", "stderr"):
            if key in row and (not isinstance(row[key], str) or len(row[key].encode()) > 524288):
                raise SshStepError(
                    "SSH_CONFIG", f"Simulation « {row['id']} » : « {key} » est un texte de 512 Ko au plus."
                )
        status = row.get("exit_status")
        if status is not None and (isinstance(status, bool) or not isinstance(status, int) or not 0 <= status <= 255):
            raise SshStepError("SSH_CONFIG", f"Simulation « {row['id']} » : « exit_status » de 0 à 255.")
        out[row["id"]] = {k: row[k] for k in ("stdout", "stderr", "exit_status", "failure") if k in row}
    return out


# ------------------------------------------------------------------------------------------------ results
def blank_result(item_id: str, kind: str, combined: bool = False) -> dict:
    res: dict = {
        "id": item_id,
        "kind": kind,  # command | step
        "status": "not_run",  # ok | failed | timeout | not_run
        "command": None,
        "stdout": None,
        "stderr": None,
        "exit_status": None,  # null when the server sent none: never invented
        "duration_ms": 0,
        "truncated": False,
        "timed_out": False,
        "uncertain": False,  # a remote command may have run on: nothing says it stopped
        "sensitive": False,
        "redacted": False,
        "simulated": False,
        "error": None,
    }
    if (
        kind == "step"
    ):  # shell steps: what was sent, what ended the wait, the configured interactions, and honestly which streams these are
        res.update(sent=None, matched=None, interactions=[], streams="combined" if combined else "separate")
    return res


def redact_sensitive(res: dict) -> None:
    if res.get("sensitive"):
        res["stdout"] = res["stderr"] = None
        res["redacted"] = True
        for key in ("matched", "sent", "combined_output", "interactions"):
            if key in res:
                res[key] = None if key != "interactions" else []


def build_output(spec, results, *, connected, simulated, duration_ms, failed, error=None, hops=None) -> dict:
    kind_error = error.error_type if error is not None else None
    if failed is not None and kind_error is None:
        kind_error = (failed.get("error") or {}).get("type")
    ok = failed is None and error is None
    return {
        "status": "succeeded" if ok else "failed",
        "connected": connected,
        "simulated": simulated,
        "session": spec["session"],
        "duration_ms": duration_ms,
        "hops": hops
        if hops is not None
        else [
            {"connection": n, "host_fingerprint": None, **({"host": e["host"]} if e else {})}
            for n, e in zip(
                [*spec["jumps"], spec["connection"]],
                list(spec.get("endpoints") or []) + [None] * (len(spec["jumps"]) + 1),
                strict=False,
            )
        ],
        "results": results,
        "failed_step": failed["id"] if failed is not None else None,
        "error_type": kind_error,
        "error": (
            {"type": error.error_type, "message": error.message, **error.details}
            if error is not None
            else (failed.get("error") if failed is not None else None)
        ),
        "truncated": any(r.get("truncated") for r in results),
        "uncertain": any(r.get("uncertain") for r in results),
        "failed_count": sum(1 for r in results if r["status"] in ("failed", "timeout") and not r.get("ignored")),
    }


def engine_error(out: dict) -> dict:
    err = out.get("error") or {"type": "internal", "message": "Échec."}
    kind = err.get("type", "internal")
    message = err.get("message", "Échec de l'étape SSH.")
    if out.get("failed_step"):
        message = f"Étape « {out['failed_step']} » : {message}"
    if out.get("uncertain"):
        message += " Une commande distante a pu se poursuivre : vérifiez côté serveur avant de relancer."
    return {
        "code": f"SSH_{kind.upper()}",
        "message": message,
        "next_step": NEXT_STEPS.get(kind, "Consultez le résultat partiel de l'étape."),
        "details": {
            "error_type": kind,
            "uncertain": bool(out.get("uncertain")),
            **{k: v for k, v in err.items() if k in ("hop", "connection")},
        },
    }


NEXT_STEPS = {
    "dns": "Vérifiez le nom du serveur dans la connexion.",
    "blocked": "Vérifiez la connexion et la liste d'adresses autorisées de la plateforme.",
    "connect": "Vérifiez que le serveur est joignable.",
    "timeout": "Augmentez le délai ou vérifiez la commande.",
    "auth": "Vérifiez l'identifiant et le secret de la connexion.",
    "hostkey": "Vérifiez le serveur : la clé ne correspond pas à l'empreinte enregistrée (usurpation possible).",
    "jump": "Vérifiez le rebond : il n'a pas pu ouvrir la connexion vers le serveur suivant.",
    "expect": "Vérifiez le motif attendu et le délai.",
    "remote_status": "Consultez le résultat de la commande.",
    "output_limit": "Réduisez la sortie de la commande ou relevez la limite.",
    "cancelled": "",
    "interrupted": "Vérifiez côté serveur avant de relancer.",
    "closed": "Le serveur a fermé la session : vérifiez-la et relancez après contrôle côté serveur.",
}


def _mask_value(value, secrets, mask_text):
    if isinstance(value, str):
        return mask_text(value, secrets)
    if isinstance(value, dict):
        return {k: _mask_value(v, secrets, mask_text) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v, secrets, mask_text) for v in value]
    return value


def mask_output(out: dict, secrets: list, mask_text) -> dict:
    """Known secret values never survive in a result (this does not detect other sensitive data a server may send back)."""
    useful = [s for s in secrets if s and len(s) >= 4]
    return _mask_value(out, useful, mask_text) if useful else out


# ------------------------------------------------------------------------------------------------ the cancel / lease check
def step_checker(sr):
    """`check()` -> True when the step is no longer ours (run cancelled, lease lost). Also renews the lease while the session lasts."""
    from django.utils import timezone

    from .models import StepRun

    state = {"last": 0.0, "beat": time.monotonic()}

    def check() -> bool:
        now = time.monotonic()
        if now - state["last"] < 1.0:
            return False
        state["last"] = now
        live = StepRun.objects.filter(pk=sr.pk, status=StepRun.Status.WAITING, http_job__state="running")
        row = live.first()
        if row is None:
            return True
        if now - state["beat"] >= 30:  # the engine treats a job running longer than its lease as interrupted
            state["beat"] = now
            live.update(http_job={**(row.http_job or {}), "claimed_at": timezone.now().isoformat()})
        return False

    return check


# ------------------------------------------------------------------------------------------------ connections
@dataclass
class Hop:
    name: str
    host: str
    port: int
    user: str
    secret: str
    passphrase: str
    fingerprints: list
    connect_timeout: int
    auth_timeout: int
    from_step: bool = False  # the host was chosen by the step (the connection is only a reusable identity)

    @property
    def secret_values(self) -> list:
        return [self.secret, self.passphrase]


@dataclass
class Hops(list):
    alias_values: dict = field(default_factory=dict)


def load_hops(spec, workspace, environment_id, find_connection) -> Hops:
    """Reads, AT EXECUTION TIME and in the run's workspace, every connection of the chain and the secrets of the declared aliases."""
    from vault.models import Secret

    hops = Hops()
    endpoints = spec.get("endpoints") or []
    for index, name in enumerate([*spec["jumps"], spec["connection"]]):
        endpoint = endpoints[index] if index < len(endpoints) else None
        conn = find_connection(workspace.pk, environment_id, name)
        if conn is None or conn.secret is None:
            raise SshStepError(
                "CONNECTION_MISSING", f"Connexion « {name} » introuvable ou sans secret.", "Vérifiez la connexion."
            )
        cfg = conn.config or {}
        user, secret = conn.login()
        passphrase = ""
        if cfg.get("passphrase_secret"):
            sec = Secret.objects.filter(workspace=workspace, name=cfg["passphrase_secret"]).first()
            if sec is None:
                raise SshStepError("SSH_CONFIG", f"Secret de la phrase secrète introuvable pour « {name} ».")
            passphrase = sec.reveal()
        if endpoint is not None:
            # A host chosen by the step may only be reached with a PRIVATE KEY: authenticating with a key never hands the key to the server, whereas a
            # password sent to a host that a workflow author names would. (Checked here on the real secret: it may come from an external vault.)
            if not secret.lstrip().startswith("-----BEGIN"):
                raise SshStepError(
                    "SSH_CONFIG",
                    f"La connexion « {name} » porte un mot de passe : il ne peut pas être envoyé à un hôte choisi par l'étape. "
                    "Utilisez une clé privée (connexion sans hôte) ou un hôte défini dans la connexion.",
                )
        hops.append(
            Hop(
                name=conn.name,
                host=endpoint["host"] if endpoint else str(cfg.get("host", "")).strip().lower(),
                port=endpoint["port"] if endpoint else int(cfg.get("port") or 22),
                user=user,
                secret=secret,
                passphrase=passphrase,
                fingerprints=endpoint["fingerprints"] if endpoint else pinned_fingerprints(cfg),
                connect_timeout=int(cfg.get("connect_timeout_seconds") or 10),
                auth_timeout=int(cfg.get("auth_timeout_seconds") or 10),
                from_step=endpoint is not None,
            )
        )
    for alias, secret_name in spec.get("secrets", {}).items():
        sec = Secret.objects.filter(workspace=workspace, name=secret_name).first()
        if sec is None:
            raise SshStepError("SSH_CONFIG", f"Secret « {secret_name} » (alias « {alias} ») introuvable.")
        hops.alias_values[alias] = sec.reveal()
    return hops


class Session:
    """The opened chain. `close()` closes the transports from the last hop back to the first."""

    def __init__(self, transports, hops_info):
        self.transports, self.hops_info = transports, hops_info

    @property
    def target(self):
        return self.transports[-1]

    def close(self):
        for t in reversed(self.transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001, S110 - closing must never hide the result
                pass
        self.transports = []


def open_chain(hops, spec, deadline, cancel_check, connector=None) -> Session:
    """Opens every hop in order. Only a transient failure (TCP connect, banner, timeout) is retried, and only here, before any command."""
    attempts = spec["connect_retries"] + 1
    for attempt in range(1, attempts + 1):
        if cancel_check():
            raise SshStepError("SSH_CANCELLED", "Étape annulée.", "")
        try:
            return _open_once(hops, deadline, cancel_check, connector)
        except SshStepError as exc:
            if exc.error_type not in TRANSIENT or attempt == attempts or time.monotonic() + attempt >= deadline:
                raise
            time.sleep(min(attempt, max(0.0, deadline - time.monotonic())))
    raise AssertionError("unreachable")  # pragma: no cover


def _open_once(hops, deadline, cancel_check, connector) -> Session:
    import paramiko

    transports: list = []
    info: list = []
    sock = None
    try:
        for i, hop in enumerate(hops):
            if cancel_check():  # also renews the lease between hops, and stops a connection nobody waits for any more
                raise SshStepError("SSH_CANCELLED", "Étape annulée.", "")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SshStepError(
                    "SSH_TIMEOUT", "Délai global dépassé pendant la connexion.", hop=i, connection=hop.name
                )
            ctimeout = max(1.0, min(hop.connect_timeout, remaining))
            if i == 0:
                sock = _first_socket(hop, ctimeout, connector)
            else:
                prev = transports[-1]
                if not valid_host(hop.host):
                    raise SshStepError("SSH_BLOCKED", "Adresse du rebond refusée.", hop=i, connection=hop.name)
                try:
                    # the previous hop resolves the name: the platform's own DNS and network guard do not apply beyond the first hop
                    sock = prev.open_channel("direct-tcpip", (hop.host, hop.port), ("127.0.0.1", 0), timeout=ctimeout)
                except (paramiko.SSHException, EOFError, OSError) as exc:
                    raise SshStepError(
                        "SSH_JUMP",
                        f"Le rebond précédent n'a pas pu joindre « {hop.name} » ({type(exc).__name__}).",
                        hop=i,
                        connection=hop.name,
                    ) from None
            transport = paramiko.Transport(sock)
            transports.append(transport)
            transport.banner_timeout = ctimeout
            transport.auth_timeout = max(1.0, min(hop.auth_timeout, deadline - time.monotonic()))
            kind = (
                "SSH_JUMP" if i else "SSH_CONNECT"
            )  # beyond the first hop a failure to talk to the next server is the jump's
            try:
                transport.start_client(timeout=ctimeout)
            except paramiko.SSHException as exc:
                raise SshStepError(
                    kind,
                    f"Négociation SSH impossible avec « {hop.name} » ({type(exc).__name__}).",
                    hop=i,
                    connection=hop.name,
                ) from None
            except (EOFError, OSError) as exc:
                raise SshStepError(
                    kind,
                    f"Connexion SSH interrompue avec « {hop.name} » ({type(exc).__name__}).",
                    hop=i,
                    connection=hop.name,
                ) from None
            seen = fingerprint(transport.get_remote_server_key())
            if seen not in hop.fingerprints:
                raise SshStepError(
                    "SSH_HOSTKEY",
                    f"La clé du serveur « {hop.name} » ne correspond à aucune empreinte enregistrée.",
                    hop=i,
                    connection=hop.name,
                )
            _authenticate(transport, hop, i)
            transport.set_keepalive(15)
            info.append(
                {"connection": hop.name, "host_fingerprint": seen, **({"host": hop.host} if hop.from_step else {})}
            )
        return Session(transports, info)
    except BaseException:
        for t in reversed(transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001, S110
                pass
        raise


def _first_socket(hop, timeout, connector):
    if connector is not None:  # tests only: a stand-in for the TCP connection (no name resolution there either)
        return connector(hop, timeout)
    try:
        target = netsec.validate_url(f"https://{hop.host}:{hop.port}/")
    except netsec.UnsafeURL as exc:
        kind = "dns" if str(exc) == DNS_MESSAGE else "blocked"
        raise SshStepError(f"SSH_{kind.upper()}", f"Serveur refusé : {exc}", hop=0, connection=hop.name) from None
    try:
        return netsec.open_connection(target, timeout)
    except TimeoutError:
        raise SshStepError(
            "SSH_TIMEOUT", f"Délai de connexion dépassé vers « {hop.name} ».", hop=0, connection=hop.name
        ) from None
    except OSError as exc:
        raise SshStepError(
            "SSH_CONNECT",
            f"Connexion impossible vers « {hop.name} » ({type(exc).__name__}).",
            hop=0,
            connection=hop.name,
        ) from None


def load_private_key(text: str, passphrase: str = ""):
    """The private key held by a connection (Ed25519, ECDSA or RSA; OpenSSH, PKCS#1 and PKCS#8 text, protected by `passphrase` or not), or
    None when it cannot be read. `paramiko.PKey.from_private_key` cannot be used: on the installed version the base class is not
    instantiable. Formats paramiko does not read itself (PKCS#8) are converted in memory by `cryptography`; nothing is written to disk."""
    import paramiko

    classes = (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey)
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives import serialization

    try:
        private = serialization.load_pem_private_key(
            text.encode(), password=passphrase.encode() if passphrase else None
        )
        plain = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        ).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return None
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(plain))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    return None


def _authenticate(transport, hop: Hop, index: int) -> None:
    import paramiko

    try:
        if hop.secret.lstrip().startswith("-----BEGIN"):
            pkey = load_private_key(hop.secret, hop.passphrase)
            if pkey is None:
                raise SshStepError(
                    "SSH_AUTH",
                    f"Clé privée de « {hop.name} » illisible ou phrase secrète incorrecte.",
                    hop=index,
                    connection=hop.name,
                )
            transport.auth_publickey(hop.user, pkey)  # only this credential: no agent, no ~/.ssh
        else:
            transport.auth_password(
                hop.user, hop.secret.rstrip("\r\n")
            )  # a password pasted in a multi-line field may carry a final newline
    except paramiko.AuthenticationException:
        raise SshStepError(
            "SSH_AUTH", f"Authentification refusée par « {hop.name} ».", hop=index, connection=hop.name
        ) from None
    except (paramiko.SSHException, EOFError, OSError) as exc:
        raise SshStepError(
            "SSH_CONNECT",
            f"Authentification interrompue avec « {hop.name} » ({type(exc).__name__}).",
            hop=index,
            connection=hop.name,
        ) from None


# ------------------------------------------------------------------------------------------------ exec sessions
def _decode(buf) -> str:
    return bytes(buf).decode("utf-8", errors="replace")


def run_exec(session, spec, deadline, cancel_check, started) -> dict:
    results, failed, abort, total = [], None, None, 0
    for cmd in spec["commands"]:
        if abort is not None or (failed is not None and spec["stop_on_error"]):
            results.append(
                {
                    **blank_result(cmd["id"], "command"),
                    "status": "not_run",
                    "command": MASKED if cmd["sensitive"] else cmd["run"],
                    "sensitive": cmd["sensitive"],
                }
            )
            continue
        res, abort = _exec_one(session, cmd, spec, deadline, cancel_check, total)
        total += res.pop("_size", 0)
        res["ignored"] = bool(cmd["ignore_error"]) and res["status"] in ("failed", "timeout")
        results.append(res)
        if res["status"] in ("failed", "timeout") and not cmd["ignore_error"]:
            failed = failed or res
    error = abort
    return build_output(
        spec,
        results,
        connected=True,
        simulated=False,
        duration_ms=int((time.monotonic() - started) * 1000),
        failed=failed,
        error=error,
        hops=session.hops_info,
    )


def end_global(deadline) -> float:
    return deadline - time.monotonic()


def bounded(chan, seconds: float, call):
    """Runs a paramiko call that waits without a timeout (exec / pty / shell requests) and closes the channel if it does not answer in time,
    which wakes the wait with an error: a server that opens a channel and never answers cannot hold the worker."""
    import threading

    timer = threading.Timer(seconds, chan.close)
    timer.daemon = True
    timer.start()
    try:
        return call()
    finally:
        timer.cancel()


class _CommandWatch:
    """The wait of one running command: reads both streams (bounded) and decides when to stop. Each `check_*` method that ends the command
    records why on the result (`status`, `error`, `uncertain`) and returns True."""

    def __init__(self, chan, res, cap, total_so_far, cancel_check, end, limit):
        self.chan, self.res, self.cap, self.total_so_far = chan, res, cap, total_so_far
        self.cancel_check, self.end, self.limit = cancel_check, end, limit
        self.out, self.err = bytearray(), bytearray()
        self.abort = None
        self.grace = None

    def read(self) -> bool:
        """Moves what is available into the buffers, never past the limit (bounded memory). True when something was read."""
        moved = False
        for ready, recv, sink in (
            (self.chan.recv_ready, self.chan.recv, self.out),
            (self.chan.recv_stderr_ready, self.chan.recv_stderr, self.err),
        ):
            while ready():
                room = self.cap + 1 - len(sink)
                if room <= 0:  # already past the limit: stop reading, the check below ends the command
                    break
                data = recv(min(65536, room))
                if not data:
                    break
                moved = True
                sink += data
        return moved

    def over_the_limit(self) -> bool:
        out, err = self.out, self.err
        if not (len(out) > self.cap or len(err) > self.cap or self.total_so_far + len(out) + len(err) > MAX_TOTAL):
            return False
        res = self.res
        res["truncated"], res["uncertain"], res["status"] = True, True, "failed"
        res["error"] = error_dict("output_limit", "Limite de sortie atteinte : la sortie est tronquée.", uncertain=True)
        del out[self.cap :], err[self.cap :]
        return True

    def cancelled(self) -> bool:
        if not self.cancel_check():
            return False
        self.res["uncertain"], self.res["status"] = True, "failed"
        self.res["error"] = error_dict(
            "cancelled", "Annulée : la commande distante a pu se poursuivre.", uncertain=True
        )
        self.abort = SshStepError("SSH_CANCELLED", "Étape annulée.", "")
        return True

    def timed_out(self) -> bool:
        if time.monotonic() < self.end:
            return False
        self.res.update(status="timeout", timed_out=True, uncertain=True)
        self.res["error"] = error_dict(
            "timeout",
            f"Délai de {int(self.limit)} s dépassé : la commande distante a pu se poursuivre.",
            uncertain=True,
        )
        return True

    def stream_ended(self) -> bool:
        chan = self.chan
        pending = chan.recv_ready() or chan.recv_stderr_ready()
        if pending:
            return False
        if chan.closed or (chan.eof_received and chan.exit_status_ready()):
            return True
        if chan.eof_received:
            self.grace = self.grace or time.monotonic() + 1.0  # the server closed the stream without an exit status
            return time.monotonic() >= self.grace
        return False

    def wait(self) -> None:
        while True:
            moved = self.read()
            if self.over_the_limit() or self.cancelled() or self.timed_out() or self.stream_ended():
                return
            if not moved:
                time.sleep(POLL)


def _conclude_command(res, cmd, watch, started, status, clean_end) -> None:
    """The result of a command whose wait is over: duration, exit status, output (unless sensitive), and the verdict."""
    res["duration_ms"] = int((time.monotonic() - started) * 1000)
    res["exit_status"] = status if isinstance(status, int) and status >= 0 else None
    if not cmd["sensitive"]:
        res["stdout"], res["stderr"] = _decode(watch.out), _decode(watch.err)
    res["_size"] = len(watch.out) + len(watch.err)
    if res["status"] == "not_run" and res["exit_status"] is None and not clean_end:
        # the channel or the connection went away without a clean end of stream: that is not a success with « no status »
        res.update(status="failed", uncertain=True)
        res["error"] = error_dict(
            "closed", "Connexion coupée pendant la commande : elle a pu se poursuivre.", uncertain=True
        )
    if res["status"] == "not_run":  # the loop ended normally
        if res["exit_status"] is None or res["exit_status"] in cmd["expect_exit"]:
            res["status"] = "ok"
        else:
            res["status"] = "failed"
            res["error"] = error_dict("remote_status", f"Statut de sortie {res['exit_status']} non attendu.")
    redact_sensitive(res)


def _start_command(chan, cmd, res, deadline, cancel_check):
    """Sends the exec request. A `(result, error)` pair when the command ends here (cancelled, request failed), else None."""
    if cancel_check():  # the step is no longer ours (cancelled, lease lost): no command is started
        res["status"] = "not_run"
        return res, SshStepError("SSH_CANCELLED", "Étape annulée.", "")
    try:
        bounded(chan, min(10, max(1.0, end_global(deadline))), lambda: chan.exec_command(cmd["run"]))
    except Exception as exc:  # noqa: BLE001 - the request may or may not have reached the server
        res["status"], res["uncertain"] = "failed", True
        res["error"] = error_dict(
            "closed", f"Canal fermé avant la confirmation de la commande ({type(exc).__name__}).", uncertain=True
        )
        return res, None
    return None


def _exec_one(session, cmd, spec, deadline, cancel_check, total_so_far):
    res = blank_result(cmd["id"], "command")
    res.update(command=MASKED if cmd["sensitive"] else cmd["run"], sensitive=cmd["sensitive"])
    started = time.monotonic()
    remaining = deadline - started
    if remaining <= 0:
        res["status"] = "not_run"
        return res, SshStepError("SSH_TIMEOUT", "Délai global dépassé avant cette commande.")
    limit = min(cmd["timeout"] or spec["op_timeout"], remaining)
    try:
        chan = session.target.open_session(timeout=min(limit, 10))
    except Exception as exc:  # noqa: BLE001
        res["status"], res["error"] = "failed", error_dict("connect", f"Canal SSH refusé ({type(exc).__name__}).")
        return res, None
    try:
        refused = _start_command(chan, cmd, res, deadline, cancel_check)
        if refused:
            return refused
        watch = _CommandWatch(chan, res, spec["max_output"], total_so_far, cancel_check, started + limit, limit)
        watch.wait()
        status = chan.recv_exit_status() if chan.exit_status_ready() else None
        clean_end = (status is not None and status >= 0) or bool(
            chan.eof_received and session.target.is_active()
        )  # paramiko reports -1 when the channel just vanished
    finally:
        try:
            chan.close()
        except Exception:  # noqa: BLE001, S110
            pass
    _conclude_command(res, cmd, watch, started, status, clean_end)
    return res, watch.abort
