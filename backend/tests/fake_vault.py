"""A small in-process HashiCorp-Vault look-alike: AppRole login, KV v2 (versions), dynamic database credentials, lease
revocation, sys/health. Enough for the client's behaviour (token expiry, permission denied, namespaces) without a container."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROLE_ID, SECRET_ID = "role-abc", "secret-xyz-123"


class FakeVault:
    def __init__(self):
        self.kv: dict[str, list[dict]] = {}  # "mount/path" -> versions
        self.tokens: set[str] = set()
        self.logins = 0
        self.requests: list[tuple[str, str, str]] = []  # (method, path, namespace)
        self.forbidden: set[str] = set()  # "mount/path" prefixes the policy refuses
        self.sealed = False
        self.lease_seconds = 60
        self.creds_issued = 0
        self.revoked: list[str] = []
        self.token_ttl = 3600
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.address = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()

    def expire_tokens(self):
        self.tokens.clear()

    def _handler(vault):  # noqa: N805
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body=None):
                raw = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def _route(self, method):
                url = urlsplit(self.path)
                path = url.path.removeprefix("/v1/")
                query = parse_qs(url.query)
                body = self._body() if method in ("POST", "PUT") else {}
                vault.requests.append((method, path, self.headers.get("X-Vault-Namespace", "")))
                if path == "sys/health":
                    return self._send(200, {"initialized": True, "sealed": vault.sealed, "version": "1.17.0"})
                if path == "auth/approle/login":
                    if body.get("role_id") == ROLE_ID and body.get("secret_id") == SECRET_ID:
                        vault.logins += 1
                        token = f"tok-{vault.logins}"
                        vault.tokens.add(token)
                        return self._send(200, {"auth": {"client_token": token, "lease_duration": vault.token_ttl}})
                    return self._send(400, {"errors": ["invalid role or secret ID"]})
                if self.headers.get("X-Vault-Token") not in vault.tokens:
                    return self._send(403, {"errors": ["permission denied"]})
                if any(path.startswith(p) or path.replace("/data/", "/", 1).startswith(p) for p in vault.forbidden):
                    return self._send(403, {"errors": ["1 error occurred: * permission denied"]})
                if path == "sys/leases/revoke" and method == "PUT":
                    vault.revoked.append(body.get("lease_id", ""))
                    return self._send(204)
                if path.startswith("database/creds/") and method == "GET":
                    vault.creds_issued += 1
                    n = vault.creds_issued
                    return self._send(
                        200,
                        {
                            "lease_id": f"{path}/lease-{n}",
                            "lease_duration": vault.lease_seconds,
                            "data": {"username": f"v-app-{n}", "password": f"pw-dyn-{n}"},
                        },
                    )
                parts = path.split("/", 2)
                if len(parts) == 3 and parts[1] in ("data", "metadata"):
                    key = f"{parts[0]}/{parts[2]}"
                    versions = vault.kv.setdefault(key, [])
                    if parts[1] == "data" and method == "POST":
                        versions.append(dict(body.get("data") or {}))
                        return self._send(200, {"data": {"version": len(versions)}})
                    if parts[1] == "data" and method == "GET":
                        if not versions:
                            vault.kv.pop(key, None)
                            return self._send(404, {"errors": []})
                        want = int(query.get("version", [len(versions)])[0])
                        if not 1 <= want <= len(versions):
                            return self._send(404, {"errors": []})
                        return self._send(200, {"data": {"data": versions[want - 1], "metadata": {"version": want}}})
                    if parts[1] == "metadata" and method == "DELETE":
                        vault.kv.pop(key, None)
                        return self._send(204)
                return self._send(404, {"errors": ["unsupported path"]})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

            def do_DELETE(self):
                self._route("DELETE")

        return H
