#!/usr/bin/env python3
"""Minimal secret scanner over git-tracked files (no external service). Exit 1 on findings."""
import re
import subprocess
import sys

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "generic secret assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{20,}['\"]"),
}
ALLOW = ("fluxonata-dev", "demo-fluxonata", "pw-test-123")  # documented throwaway dev values
files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
bad = 0
for f in files:
    if f.endswith((".png", ".lock", "package-lock.json")) or f.startswith("docs/SBOM"):
        continue
    try:
        text = open(f, encoding="utf-8").read()
    except (UnicodeDecodeError, FileNotFoundError):
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if "scan:allow" in line or any(a in line for a in ALLOW):  # explicit marker for fake test fixtures
            continue
        for name, rx in PATTERNS.items():
            if rx.search(line):
                print(f"{f}:{i}: {name}")
                bad += 1
print("secrets scan:", "FAIL" if bad else "ok", f"({len(files)} fichiers)")
sys.exit(1 if bad else 0)
