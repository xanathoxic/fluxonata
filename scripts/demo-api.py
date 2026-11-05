#!/usr/bin/env python3
"""Petite API de démonstration pour les contrôles lus depuis un service (données fictives, aucun secret, lecture seule).

    GET /marques?q=<texte>   les marques (groupe « US Brands », options désactivées), filtrées par `q`
    GET /selection           une présélection : {"selection": ["saab", "ford"]}

Lancée par scripts/dev-up.sh sur 127.0.0.1:8765. Le serveur de la plateforme ne l'appelle que si cette adresse figure dans
ORCH_EGRESS_ALLOWLIST (environnement de développement : à décider par l'opérateur)."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

BRANDS = [
    {"id": "saab", "name": "Saab"},
    {"id": "opel", "name": "Opel"},
    {"id": "volvo", "name": "Volvo"},
    {"id": "mercedes", "name": "Mercedes", "off": True},
    {"id": "chrysler", "name": "Chrysler", "region": "US Brands"},
    {"id": "general-motors", "name": "General Motors", "region": "US Brands"},
    {"id": "ford", "name": "Ford", "region": "US Brands"},
    {"id": "tesla", "name": "Tesla", "region": "US Brands"},
    {"id": "plymouth", "name": "Plymouth", "region": "US Brands", "off": True},
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        if url.path == "/marques":
            q = (parse_qs(url.query).get("q") or [""])[0].strip().lower()
            body = {"data": {"rows": [b for b in BRANDS if q in b["name"].lower() or q in b.get("region", "").lower()]}}
        elif url.path == "/selection":
            body = {"selection": ["saab", "ford"]}
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

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
