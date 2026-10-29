import http.server
import threading

import pytest

from core import netsec
from core.netsec import UnsafeURL, safe_request, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://192.168.0.1/x",
        "http://172.16.5.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://[fd00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
        "file:///etc/passwd",
        "gopher://x/",
        "ftp://example.com/",
        "http://user:pw@example.com/",
        "http:///nohost",
    ],
)
def test_internal_and_dangerous_urls_are_rejected(url):
    with pytest.raises(UnsafeURL):
        validate_url(url)


def test_hostname_resolving_to_internal_address_is_rejected(monkeypatch):
    monkeypatch.setattr(netsec, "resolve", lambda host: ["10.0.0.5"])
    with pytest.raises(UnsafeURL, match="interne"):
        validate_url("http://innocent.example.com/")
    monkeypatch.setattr(netsec, "resolve", lambda host: ["93.184.216.34", "127.0.0.1"])  # one bad answer is enough
    with pytest.raises(UnsafeURL):
        validate_url("http://mixed.example.com/")


def test_public_url_is_accepted_and_pinned_to_the_validated_ip(monkeypatch):
    monkeypatch.setattr(netsec, "resolve", lambda host: ["93.184.216.34"])
    t = validate_url("https://example.com:8443/a?b=1")
    assert (t.scheme, t.host, t.port, t.path, t.ip) == ("https", "example.com", 8443, "/a?b=1", "93.184.216.34")


def test_unknown_host_is_a_clean_error():
    with pytest.raises(UnsafeURL, match="introuvable"):
        validate_url("http://this-host-does-not-exist.invalid/")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        data = b"hello" * 100000
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_loopback_is_blocked_unless_explicitly_allowlisted(local_server, settings):
    with pytest.raises(UnsafeURL):
        safe_request("GET", f"http://127.0.0.1:{local_server}/")
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    status, _, body = safe_request("GET", f"http://127.0.0.1:{local_server}/", max_bytes=1000)
    assert status == 200 and len(body) == 1000  # response size is capped


def test_redirects_are_never_followed(local_server, settings):
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    status, headers, _ = safe_request("GET", f"http://127.0.0.1:{local_server}/redirect")
    assert status == 302 and "169.254.169.254" in headers["Location"]  # returned as-is, not requested


def test_host_header_carries_a_non_default_port():
    from core.netsec import Target, _host_header

    assert _host_header(Target("https", "api.example.org", 443, "/", "1.2.3.4")) == "api.example.org"
    assert _host_header(Target("http", "api.example.org", 80, "/", "1.2.3.4")) == "api.example.org"
    assert _host_header(Target("http", "127.0.0.1", 8080, "/", "127.0.0.1")) == "127.0.0.1:8080"
    assert _host_header(Target("https", "idp.example.org", 8443, "/", "1.2.3.4")) == "idp.example.org:8443"
