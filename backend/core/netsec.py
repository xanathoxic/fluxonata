"""Outbound-request safety (SSRF): every configurable URL goes through here.

Rules: http/https only, no credentials in the URL, every resolved address must be public (no loopback, private,
link-local, metadata, multicast...) unless the host is explicitly allowlisted (ORCH_EGRESS_ALLOWLIST), redirects
are never followed, and the connection goes to the *validated IP* (no second DNS lookup => no DNS rebinding).
"""

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

BLOCKED = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "2001:db8::/32",
    )
]


class UnsafeURL(ValueError):
    pass


@dataclass
class Target:
    scheme: str
    host: str
    port: int
    path: str
    ip: str


def _allowlist() -> set[str]:
    return {h.strip().lower() for h in getattr(settings, "ORCH_EGRESS_ALLOWLIST", "").split(",") if h.strip()}


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not any(addr in net for net in BLOCKED)


def resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except socket.gaierror:
        raise UnsafeURL("Nom d'hôte introuvable.") from None


def validate_url(url: str) -> Target:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("Seuls les schémas http et https sont autorisés.")
    if parts.username or parts.password:
        raise UnsafeURL("Les identifiants dans l'URL sont interdits.")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURL("URL sans hôte.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UnsafeURL("Port invalide.") from None
    ips = [host] if _is_ip(host) else resolve(host)
    if host not in _allowlist():
        bad = [ip for ip in ips if not is_public(ip)]
        if bad:
            raise UnsafeURL("Cette adresse est interne ou réservée : elle n'est pas autorisée (SSRF).")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Target(parts.scheme, host, port, path, ips[0])


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout)
        self._ip = target.ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout, context=ssl.create_default_context())
        self._ip = target.ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def safe_request(
    method: str, url: str, *, headers=None, body: bytes | None = None, timeout: float = 5, max_bytes: int = 65536
):
    """Returns (status, response_headers, body_bytes). Never follows redirects; body truncated at max_bytes."""
    target = validate_url(url)
    conn = (_PinnedHTTPS if target.scheme == "https" else _PinnedHTTP)(target, timeout)
    try:
        conn.request(
            method,
            target.path,
            body=body,
            headers={"Host": target.host, "User-Agent": "fluxonata/0.1", **(headers or {})},
        )
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read(max_bytes)
    finally:
        conn.close()
