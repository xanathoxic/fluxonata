"""scripts/notify-failure.sh : notification d'échec d'une tâche planifiée, éprouvée contre une destination FACTICE locale
(serveur sur 127.0.0.1) : aucun message réel n'est envoyé, aucun service n'est activé."""

import http.server
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notify-failure.sh"


class _Sink(http.server.BaseHTTPRequestHandler):
    received: list = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).received.append((self.path, self.headers.get("Content-Type"), json.loads(body)))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def sink():
    _Sink.received, _Sink.status = [], 200
    server = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _Sink
    server.shutdown()
    server.server_close()


def run(unit="fluxonata-restore-test.service", url=None):
    env = {k: v for k, v in os.environ.items() if k != "FLUXONATA_NOTIFY_URL"}
    if url is not None:
        env["FLUXONATA_NOTIFY_URL"] = url
    return subprocess.run([str(SCRIPT), unit], capture_output=True, text=True, env=env, timeout=60, check=False)


def test_a_configured_destination_receives_one_json_message_naming_the_unit(sink):
    server, handler = sink
    secret = f"hook-{uuid.uuid4().hex}"  # valeur aléatoire à chaque essai : aucun secret littéral
    url = f"http://127.0.0.1:{server.server_port}/hook/{secret}"
    p = run(url=url)
    assert p.returncode == 0, p.stderr
    assert len(handler.received) == 1
    path, ctype, body = handler.received[0]
    assert path == f"/hook/{secret}" and ctype == "application/json"
    assert "fluxonata-restore-test.service" in body["text"] and "a échoué" in body["text"]
    assert secret not in p.stderr and secret not in p.stdout  # l'adresse est un secret : jamais au journal


def test_without_a_destination_only_the_journal_line_signals_the_failure(sink):
    _, handler = sink
    p = run()
    assert p.returncode == 0
    assert "fluxonata-restore-test.service" in p.stderr and "Aucune destination" in p.stderr
    assert handler.received == []


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "file:///etc/passwd", "127.0.0.1:80/x"])
def test_a_destination_that_is_not_http_is_refused_before_any_request(url, sink):
    _, handler = sink
    p = run(url=url)
    assert p.returncode == 2 and "refusée" in p.stderr
    assert handler.received == []


def test_an_unreachable_or_refusing_destination_fails_without_logging_the_address(sink):
    server, handler = sink
    handler.status = 500
    secret = f"autre-{uuid.uuid4().hex}"
    p = run(url=f"http://127.0.0.1:{server.server_port}/{secret}")
    assert p.returncode == 1 and "Envoi impossible" in p.stderr and secret not in p.stderr
    p = run(url=f"http://127.0.0.1:9/{secret}")  # port fermé
    assert p.returncode == 1 and secret not in p.stderr
