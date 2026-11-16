"""A disposable SSH server for tests: an in-process `paramiko` server on 127.0.0.1 with synthetic accounts and keys.

It is a controlled double, not a model of any real system: its tiny command language exists only to exercise the SSH step
(`echo`, `err`, `fail`, `exit`, `sleep`, `big`, `hang`, `noexit`, `whoami`) and a scripted shell (prompt, `passwd`, `confirm`, `big`, `slow`…).
It records what it received (`exec_log`, `connections`) so tests can prove what was — and was not — sent. It can forward `direct-tcpip`
channels, which is what makes jump chains testable with several instances."""

import io
import select
import socket
import threading
import time

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from runs.ssh_core import fingerprint


def new_key():
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
    ).decode()
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem)), pem


def new_encrypted_pem(passphrase: str):
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.BestAvailableEncryption(passphrase.encode()),
    ).decode()
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem), password=passphrase), pem


class FakeSshd:
    def __init__(self, users=None, forward=True, shell_password="s3cret-shell", prompt=("host> ",), slow_banner=0.0):
        self.host_key, _ = new_key()
        self.fingerprint = fingerprint(self.host_key)
        self.users = users or {}  # name -> {"password": str | None, "keys": [PKey]}
        self.forward, self.shell_password, self.prompt, self.slow_banner = forward, shell_password, prompt, slow_banner
        self.exec_log: list[tuple[str, str]] = []  # (user, command)
        self.connections: list[dict] = []
        self.forwards: list[tuple[str, int]] = []  # relays actually opened
        self.forward_requests: list[tuple[str, int]] = []  # every direct-tcpip request, allowed or not
        self.stop_event = threading.Event()
        self._pending: dict[int, tuple] = {}
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(20)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self.transports: list[paramiko.Transport] = []
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ lifecycle
    def stop(self):
        self.stop_event.set()
        for t in list(self.transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001, S110
                pass
        self._sock.close()

    def drop_connections(self):
        """Closes every live connection (simulates a server or network failure in the middle of a session)."""
        for t in list(self.transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------------ plumbing
    def _accept_loop(self):
        while not self.stop_event.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        record = {"user": None, "auth": None, "pty": None, "attempts": 0}
        self.connections.append(record)
        iface = _Iface(self, record)
        try:
            transport.start_server(server=iface)
        except (paramiko.SSHException, EOFError, OSError):
            return
        self.transports.append(transport)
        while transport.is_active() and not self.stop_event.is_set():
            chan = transport.accept(0.2)
            if chan is None:
                continue
            dest = self._pending.pop(chan.get_id(), None)
            if dest is not None:
                threading.Thread(target=self._forward, args=(chan, dest), daemon=True).start()

    def _forward(self, chan, dest):
        self.forwards.append(dest)
        try:
            upstream = socket.create_connection(dest, 5)
        except OSError:
            try:
                chan.close()
            except (OSError, EOFError):
                pass
            return
        try:
            while not self.stop_event.is_set() and not chan.closed:
                r, _, _ = select.select([chan, upstream], [], [], 0.2)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    upstream.sendall(data)
                if upstream in r:
                    data = upstream.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            upstream.close()
            try:
                chan.close()
            except (OSError, EOFError):
                pass

    # ------------------------------------------------------------------ exec
    def run_exec(self, chan, user, command):
        self.exec_log.append((user, command))
        verb, _, rest = command.partition(" ")
        try:
            if verb == "echo":
                chan.sendall((rest + "\n").encode())
                status = 0
            elif verb == "err":
                chan.sendall_stderr((rest + "\n").encode())
                status = 0
            elif verb == "fail":
                chan.sendall_stderr(b"boom\n")
                status = 3
            elif verb == "exit":
                status = int(rest or 0)
            elif verb == "whoami":
                chan.sendall((user + "\n").encode())
                status = 0
            elif verb == "sleep":
                self.stop_event.wait(float(rest))
                chan.sendall(b"reveille\n")
                status = 0
            elif verb == "big":
                left = int(rest)
                while left > 0 and not chan.closed:
                    n = min(left, 32768)
                    chan.sendall(b"x" * n)
                    left -= n
                status = 0
            elif verb == "hang":
                chan.sendall(b"debut\n")
                while not chan.closed and not self.stop_event.is_set():
                    time.sleep(0.05)
                return
            elif verb == "noexit":
                time.sleep(0.2)  # let the client's request be answered before the stream ends without an exit status
                chan.sendall((rest + "\n").encode())
                chan.shutdown_write()
                chan.close()
                return
            else:
                chan.sendall_stderr(b"inconnu\n")
                status = 127
            chan.send_exit_status(status)
        except (OSError, EOFError):
            return
        finally:
            time.sleep(0.05)
            try:
                chan.close()
            except Exception:  # noqa: BLE001, S110
                pass

    # ------------------------------------------------------------------ shell
    def _write(self, chan, text, delay=0.0):
        if delay:
            time.sleep(delay)
        chan.sendall(text.encode())

    def _prompt(self, chan):
        for i, chunk in enumerate(self.prompt):
            self._write(chan, chunk, 0.05 if i else 0.0)

    def _readline(self, chan, echo):
        buf = b""
        while not self.stop_event.is_set():
            if chan.closed:
                return None
            if chan.recv_ready():
                ch = chan.recv(1)
                if ch in (b"\n", b"\r"):
                    if echo:
                        chan.sendall(b"\r\n")
                    return buf.decode()
                buf += ch
                if echo:
                    chan.sendall(ch)
            else:
                time.sleep(0.01)
        return None

    def run_shell(self, chan, record):
        pty = record["pty"] is not None
        try:
            if self.slow_banner:
                time.sleep(self.slow_banner)
            self._write(chan, "Bienvenue\r\n" if pty else "Bienvenue\n")
            self._prompt(chan)
            eol = "\r\n" if pty else "\n"
            while True:
                line = self._readline(chan, echo=pty)
                if line is None:
                    return
                verb, _, rest = line.partition(" ")
                self.exec_log.append((record["user"], "shell:" + line))
                if verb == "echo":
                    self._write(chan, rest + eol)
                elif verb == "fail":
                    self._write(chan, "ERROR: boom" + eol)
                elif verb == "err":
                    chan.sendall_stderr((rest + "\n").encode())
                elif verb == "passwd":
                    self._write(chan, "Password: ")
                    typed = self._readline(chan, echo=False)
                    verdict = "ok" if typed == self.shell_password else "denied"
                    self.exec_log.append((record["user"], "passwd-result:" + verdict))  # never the typed value itself
                    self._write(chan, eol + verdict + eol)
                elif verb == "confirm":
                    self._write(chan, "Sure? [y/N] ")
                    typed = self._readline(chan, echo=pty)
                    self._write(chan, ("done" if typed == "y" else "aborted") + eol)
                elif verb == "big":
                    left = int(rest)
                    while left > 0:
                        n = min(left, 32768)
                        self._write(chan, "x" * n)
                        left -= n
                    self._write(chan, eol)
                elif verb == "slow":
                    self._write(chan, "fini" + eol, delay=float(rest))
                elif verb == "late":  # a second prompt arrives AFTER the answer: the classic stale prompt
                    self._write(chan, "tick" + eol)
                    self._prompt(chan)
                    self._write(chan, "tock" + eol, delay=0.4)
                elif verb in ("quit", "exit"):
                    chan.close()
                    return
                elif verb == "silent":
                    continue
                else:
                    self._write(chan, "?" + eol)
                if verb != "late":
                    self._prompt(chan)
                else:
                    self._prompt(chan)
        except (OSError, EOFError):
            return


class _Iface(paramiko.ServerInterface):
    def __init__(self, daemon: FakeSshd, record: dict):
        self.d, self.record = daemon, record

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        self.record["attempts"] += 1
        user = self.d.users.get(username)
        if user and user.get("password") is not None and user["password"] == password:
            self.record.update(user=username, auth="password")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.record["attempts"] += 1
        user = self.d.users.get(username)
        if user and any(k.asbytes() == key.asbytes() for k in user.get("keys", [])):
            self.record.update(user=username, auth="publickey")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.d.forward_requests.append(tuple(destination))
        allowed = self.d.forward  # True, False, or a set of destination ports this server may relay to
        if allowed is False or (isinstance(allowed, set) and destination[1] not in allowed):
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        self.d._pending[chanid] = destination  # noqa: SLF001
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.record["pty"] = {"term": term.decode() if isinstance(term, bytes) else term, "cols": width, "rows": height}
        return True

    def check_channel_exec_request(self, channel, command):
        user = self.record["user"]
        threading.Thread(target=self.d.run_exec, args=(channel, user, command.decode()), daemon=True).start()
        return True

    def check_channel_shell_request(self, channel):
        threading.Thread(target=self.d.run_shell, args=(channel, self.record), daemon=True).start()
        return True
