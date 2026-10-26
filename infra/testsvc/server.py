"""Tiny local HTTP service used by the demo connector. Lives on an internal Docker network only (no Internet)."""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            body = {"ok": True}
        elif url.path == "/echo":
            body = {"path": url.path, "query": parse_qs(url.query), "server_time": int(time.time())}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep container logs quiet
        pass


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 - inside an isolated container network
