"""Outbound-request safety (SSRF): every configurable URL goes through here.

Rules: http/https only, no credentials in the URL, every resolved address must be public (no loopback, private,
link-local, metadata, multicast...) unless the host is explicitly allowlisted (ORCH_EGRESS_ALLOWLIST), redirects
are never followed, and the connection goes to the *validated IP* (no second DNS lookup => no DNS rebinding).
"""

import http.client
import ipaddress
import re
import socket
import ssl
import time
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
    ip: str  # the preferred address (IPv4 first)
    ips: tuple[str, ...] = ()  # every validated address, in connection order


def env_allowlist() -> set[str]:
    """What the operator allowed in the environment (`ORCH_EGRESS_ALLOWLIST`): not editable from the web interface."""
    return {h.strip().lower() for h in getattr(settings, "ORCH_EGRESS_ALLOWLIST", "").split(",") if h.strip()}


# What a platform administrator adds in the web interface (`tenancy.EgressHost`), read from the database every few seconds so every
# process (API, workers) follows a change without a restart. If the database cannot be read, the last known list stays.
MANAGED_TTL = 5.0
_managed: dict = {"at": float("-inf"), "hosts": frozenset()}


def managed_allowlist() -> frozenset[str]:
    if time.monotonic() - _managed["at"] < MANAGED_TTL:
        return _managed["hosts"]
    try:
        from tenancy.models import EgressHost

        _managed["hosts"] = frozenset(EgressHost.objects.values_list("host", flat=True))
        _managed["at"] = time.monotonic()
    except Exception:  # noqa: BLE001 - no database (runner agent, early start): the environment's list still applies
        _managed["at"] = time.monotonic() - MANAGED_TTL + 1.0
    return _managed["hosts"]


def reset_allowlist_cache() -> None:
    _managed["at"] = float("-inf")


def _allowlist() -> set[str]:
    return env_allowlist() | managed_allowlist()


# Never allowed through the web interface, whatever the name: cloud metadata and link-local services, "this host", multicast and
# reserved ranges, unspecified IPv6. Internal services (loopback, private networks) are what the allowlist is for.
NEVER = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::/32",
    )
]
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def never_allowed(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in NEVER)


def check_managed_host(raw: str) -> str:
    """The normalized host an administrator may add, or UnsafeURL with the reason: a name or an IP address, nothing else (no scheme,
    port, path, wildcard or range), and no address that is never allowed."""
    host = (raw or "").strip().lower()
    if not host or len(host) > 253 or any(c in host for c in " /\\@?#,;*"):
        raise UnsafeURL("Indiquez seulement un nom d'hôte ou une adresse IP : ni schéma, ni port, ni chemin, ni joker.")
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if _is_ip(bare):
        if never_allowed(bare):
            raise UnsafeURL(
                "Cette adresse (métadonnées cloud, lien local, multidiffusion, réservée) ne peut pas être autorisée."
            )
        return bare
    if ":" in host or not HOST_RE.match(host) or host.endswith("."):
        raise UnsafeURL("Nom d'hôte invalide (lettres, chiffres, tirets et points ; pas de port).")
    if host in ("metadata.google.internal", "metadata"):
        raise UnsafeURL("Ce nom désigne un service de métadonnées : il ne peut pas être autorisé.")
    return host


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not any(addr in net for net in BLOCKED)


def _order(ip: str) -> tuple[int, str]:
    """IPv4 before IPv6: containers usually have no IPv6 route, and a name's answers come in any order."""
    try:
        return (ipaddress.ip_address(ip.split("%")[0]).version, ip)
    except ValueError:
        return (9, ip)


def resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}, key=_order)
    except socket.gaierror:
        raise UnsafeURL("Nom d'hôte introuvable.") from None


def open_connection(target: "Target", timeout: float) -> socket.socket:
    """TCP connection to the validated addresses of `target` (pinned: the name is never resolved again), one after
    the other until one answers. Every address was checked by `validate_url`, so the SSRF guard covers all of them."""
    last: OSError | None = None
    for ip in target.ips or (target.ip,):
        try:
            return socket.create_connection((ip, target.port), timeout)
        except OSError as exc:
            last = exc
    raise last or OSError("Aucune adresse à joindre.")


def is_allowlisted(host: str) -> bool:
    """True for a host the operator allowed explicitly (ORCH_EGRESS_ALLOWLIST), e.g. an internal service."""
    return host.lower() in _allowlist()


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
    if host in managed_allowlist() and host not in env_allowlist() and any(never_allowed(ip) for ip in ips):
        # a name added in the web interface that (now) answers with a metadata or link-local address: refused, whatever the name
        raise UnsafeURL("Cette adresse est interne ou réservée : elle n'est pas autorisée (SSRF).")
    if host not in _allowlist():
        bad = [ip for ip in ips if not is_public(ip)]
        if bad:
            raise UnsafeURL("Cette adresse est interne ou réservée : elle n'est pas autorisée (SSRF).")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Target(parts.scheme, host, port, path, ips[0], tuple(ips))


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout)
        self._target = target

    def connect(self):
        self.sock = open_connection(self._target, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float, context: ssl.SSLContext | None = None):
        super().__init__(target.host, target.port, timeout=timeout, context=context or ssl.create_default_context())
        self._target = target

    def connect(self):
        raw = open_connection(self._target, self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _host_header(target: Target) -> str:
    """`host` for the default port, `host:port` otherwise (a service that builds its own URLs, e.g. an identity provider's
    issuer, needs the port the caller used)."""
    default = 443 if target.scheme == "https" else 80
    host = f"[{target.host}]" if ":" in target.host else target.host
    return host if target.port == default else f"{host}:{target.port}"


def safe_request(
    method: str,
    url: str,
    *,
    headers=None,
    body: bytes | None = None,
    timeout: float = 5,
    max_bytes: int = 65536,
    ssl_context: ssl.SSLContext | None = None,
):
    """Returns (status, response_headers, body_bytes). Never follows redirects; body truncated at max_bytes.
    `ssl_context`: a caller-built TLS context (e.g. a private certificate authority); the address checks still apply."""
    target = validate_url(url)
    conn = _PinnedHTTPS(target, timeout, ssl_context) if target.scheme == "https" else _PinnedHTTP(target, timeout)
    try:
        conn.request(
            method,
            target.path,
            body=body,
            headers={"Host": _host_header(target), "User-Agent": "fluxonata/0.1", **(headers or {})},
        )
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read(max_bytes)
    finally:
        conn.close()
