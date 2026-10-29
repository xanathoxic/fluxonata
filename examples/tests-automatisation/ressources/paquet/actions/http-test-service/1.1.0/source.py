import json
import re
import urllib.request


def run(inputs):
    path = inputs["path"]
    if not re.fullmatch(r"/[A-Za-z0-9/_?=&.-]{0,200}", path):  # fixed host, constrained path: no SSRF surface
        raise ValueError("Chemin non autorisé")
    with urllib.request.urlopen("http://testsvc:8080" + path, timeout=5) as resp:  # noqa: S310 - fixed internal host
        return {"status": resp.status, "body": json.loads(resp.read())}
