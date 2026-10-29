#!/usr/bin/env python3
"""Regenerate frontend/src/components/brandIconData.ts from the Simple Icons package (CC0-1.0).

Usage: scripts/brand-icons.py          (needs npm; downloads simple-icons into a temporary directory)
Only the icons of the integrations and connection types we ship are kept (a few KB), not the whole set.
Brand names and logos remain the property of their owners; they are shown only to identify the target service."""
import json, re, subprocess, sys, tempfile, tarfile, pathlib

VERSION = "16.36.0"
# our slug -> Simple Icons slug. Services absent from Simple Icons (removed at their owners' request) get a monogram.
MAP = {
    "jira": "jira", "github": "github", "gitlab": "gitlab", "grafana": "grafana", "pagerduty": "pagerduty", "opsgenie": "opsgenie",
    "datadog": "datadog", "notion": "notion", "airtable": "airtable", "linear": "linear", "mailgun": "mailgun", "elasticsearch": "elasticsearch",
    "influxdb": "influxdb", "jenkins": "jenkins", "sentry": "sentry", "zendesk": "zendesk", "hubspot": "hubspot", "confluence": "confluence",
    "mcp": "modelcontextprotocol", "postgres": "postgresql", "mysql": "mysql",
}
root = pathlib.Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["npm", "pack", f"simple-icons@{VERSION}", "--silent"], cwd=tmp, check=True, capture_output=True)
    tgz = next(pathlib.Path(tmp).glob("simple-icons-*.tgz"))
    with tarfile.open(tgz) as t:
        t.extractall(tmp)
    pkg = pathlib.Path(tmp) / "package"
    colors = {i["title"]: i.get("hex") for i in json.loads((pkg / "data" / "simple-icons.json").read_text())} if (pkg / "data" / "simple-icons.json").exists() else {}
    out = {}
    for ours, theirs in MAP.items():
        svg = (pkg / "icons" / f"{theirs}.svg").read_text()
        title = re.search(r"<title>(.*?)</title>", svg).group(1)
        d = re.search(r'<path d="([^"]+)"', svg).group(1)
        hexes = [i["hex"] for i in json.loads((pkg / "data" / "simple-icons.json").read_text()) if i.get("slug", "") == theirs or i["title"] == title]
        out[ours] = {"title": title, "hex": hexes[0] if hexes else "334155", "path": d}
lines = [
    "// Généré par scripts/brand-icons.py à partir de Simple Icons " + VERSION + " (CC0-1.0) : ne pas modifier à la main.",
    "// Les noms et logos de marque appartiennent à leurs propriétaires ; ils ne servent qu'à identifier le service ciblé.",
    "export interface Brand { title: string; hex: string; path: string }",
    "export const BRANDS: Record<string, Brand> = " + json.dumps(out, ensure_ascii=False, indent=2) + ";",
    "",
]
(root / "frontend/src/components/brandIconData.ts").write_text("\n".join(lines))
print(f"{len(out)} icônes écrites")
