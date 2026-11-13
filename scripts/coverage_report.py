#!/usr/bin/env python3
"""Résume un rapport `coverage json` par paquet applicatif et le compare à la baseline (politique d'ingénierie du projet).

INFORMATIF : ce script n'échoue jamais à cause d'un pourcentage. Il n'écrit la baseline (`--write`) que si la suite de tests est passée
entièrement (`--pytest-exit 0`) : une couverture mesurée sur une suite en échec sous-estime le code réellement exercé.
Usage : coverage_report.py COVERAGE.json [--baseline FICHIER] [--commit SHA] [--python X.Y.Z] [--pytest-exit N] [--write]
"""

import argparse
import configparser
import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


def pct(covered: int, total: int) -> float:
    return round(100 * covered / total, 2) if total else 100.0


def summarize(data: dict) -> dict:
    """{paquet: {statements, covered, branches, covered_branches, percent}} (paquet = premier dossier du chemin) + total."""
    packages: dict[str, dict] = {}
    for path, info in data.get("files", {}).items():
        s = info["summary"]
        key = path.split("/")[0] if "/" in path else "(racine)"
        p = packages.setdefault(key, {"statements": 0, "covered": 0, "branches": 0, "covered_branches": 0})
        p["statements"] += s["num_statements"]
        p["covered"] += s["covered_lines"]
        p["branches"] += s.get("num_branches", 0)
        p["covered_branches"] += s.get("covered_branches", 0)
    total = {k: sum(p[k] for p in packages.values()) for k in ("statements", "covered", "branches", "covered_branches")}
    for p in [*packages.values(), total]:
        p["percent"] = pct(p["covered"] + p["covered_branches"], p["statements"] + p["branches"])
    return {"total": total, "packages": dict(sorted(packages.items()))}


def summarize_tests(junit_path: str) -> dict:
    """Comptes et raisons d'omission d'un rapport JUnit de pytest : {tests, passed, failed, skipped, xfailed, skip_reasons}.

    Les ignorés (`pytest.skip`) et les attendus en échec (`xfail`) sont comptés à part : ni l'un ni l'autre n'exerce le code testé.
    """
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "xfailed": 0}
    reasons: dict[str, int] = {}
    # S314 : le JUnit est produit par pytest dans l'environnement isolé du script, pas par une source externe.
    for case in ET.parse(junit_path).getroot().iter("testcase"):  # noqa: S314 - fichier JUnit produit par pytest sur cette machine
        counts["tests"] += 1
        skip = case.find("skipped")
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
        elif skip is None:
            counts["passed"] += 1
        elif skip.get("type") == "pytest.xfail":
            counts["xfailed"] += 1
        else:
            counts["skipped"] += 1
            reason = " ".join((skip.get("message") or "(sans raison)").split())[:160]
            reasons[reason] = reasons.get(reason, 0) + 1
    return {**counts, "skip_reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])))}


def read_omissions(coveragerc_path: str) -> dict:
    """Motifs exclus de la mesure (`[run] omit`) et lignes exclues (`[report] exclude_also`), tels que configurés."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(coveragerc_path, encoding="utf-8")

    def lines(section: str, option: str) -> list[str]:
        return [x.strip() for x in cp.get(section, option, fallback="").splitlines() if x.strip()]

    return {"omit": lines("run", "omit"), "exclude_also": lines("report", "exclude_also")}


def compare(baseline: dict, current: dict) -> list[tuple[str, float | None, float, float | None]]:
    """[(nom, avant, maintenant, écart)] pour chaque paquet et le total ; `avant` est None pour un paquet nouveau."""
    rows = []
    for name, cur in [*current["packages"].items(), ("TOTAL", current["total"])]:
        before = (baseline["total"] if name == "TOTAL" else baseline.get("packages", {}).get(name, {})).get("percent")
        rows.append((name, before, cur["percent"], None if before is None else round(cur["percent"] - before, 2)))
    return rows


def render(rows) -> str:
    out = [f"{'paquet':<14}{'avant':>8}{'mesuré':>9}{'écart':>8}"]
    for name, before, now, diff in rows:
        out.append(
            f"{name:<14}{'—' if before is None else f'{before:.2f}':>8}{now:>9.2f}{'' if diff is None else f'{diff:+.2f}':>8}"
        )
    return "\n".join(out)


def render_tests(tests: dict, previous: dict | None) -> str:
    out = [
        f"Tests : {tests['tests']} au total, {tests['passed']} passés, {tests['failed']} en échec, "
        f"{tests['skipped']} ignorés, {tests['xfailed']} xfail."
    ]
    if previous and isinstance(previous.get("tests"), dict) and previous["tests"].get("skipped") != tests["skipped"]:
        out.append(f"  Ignorés : {tests['skipped']} maintenant, {previous['tests'].get('skipped')} dans la baseline.")
    for reason, n in tests["skip_reasons"].items():
        out.append(f"  ignoré ×{n} : {reason}")
    if tests["skipped"] or tests["xfailed"]:
        out.append("  Le code que ces tests auraient exercé n'est pas compté dans la couverture.")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--baseline")
    ap.add_argument("--commit", default="")
    ap.add_argument("--python", default="")
    ap.add_argument("--pytest-exit", type=int, default=0)
    ap.add_argument("--junit", help="rapport JUnit de pytest : comptes des tests et raisons des ignorés")
    ap.add_argument("--coveragerc", help="configuration de coverage : motifs omis de la mesure")
    ap.add_argument(
        "--subset", action="store_true", help="mesure d'un sous-ensemble des tests : comparaison sans valeur"
    )
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(Path(a.report).read_text(encoding="utf-8"))
    current = summarize(data)
    base_path = Path(a.baseline) if a.baseline else None
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path and base_path.exists() else None
    print(render(compare(baseline, current)) if baseline else render(compare({"total": {}, "packages": {}}, current)))
    print(
        f"\nSuite de tests : {'passée entièrement' if a.pytest_exit == 0 else f'EN ÉCHEC (code {a.pytest_exit}), mesure sous-estimée'}."
    )
    tests = summarize_tests(a.junit) if a.junit and Path(a.junit).exists() else None
    omissions = read_omissions(a.coveragerc) if a.coveragerc and Path(a.coveragerc).exists() else None
    if tests:
        print(render_tests(tests, baseline))
    else:
        print("Tests : rapport JUnit non fourni, ignorés non journalisés.")
    if omissions:
        print(
            f"Hors mesure : {', '.join(omissions['omit']) or 'rien'} ; lignes exclues : {', '.join(omissions['exclude_also']) or 'aucune'}."
        )
    print(f"Fichiers mesurés : {len(data.get('files', {}))}.")
    if baseline and a.python and baseline.get("python") and baseline["python"] != a.python:
        print(
            f"Interpréteur différent de la baseline ({baseline['python']} → {a.python}) : écarts à lire avec prudence."
        )
    if a.subset:
        print(
            "MESURE PARTIELLE (sous-ensemble de tests) : les écarts ci-dessus ne disent rien de la couverture de la suite."
        )
    if a.write:
        if a.subset or a.pytest_exit != 0 or not base_path:
            print("Baseline NON écrite : la suite n'est pas passée entièrement (ou aucun fichier de baseline indiqué).")
            return 1
        doc = {
            "_doc": "Baseline de couverture (scripts/coverage.sh --write-baseline), informative : aucun seuil. Lignes + branches, code applicatif du backend "
            "hors tests et migrations ; seul le processus pytest est mesuré (les processus lancés par les tests ne le sont pas).",
            "commit": a.commit,
            "measured_at": datetime.now(UTC).strftime("%Y-%m-%d"),
            "python": a.python,
            "coverage_version": data.get("meta", {}).get("version", ""),
            "files_measured": len(data.get("files", {})),
            **({"tests": tests} if tests else {}),
            **({"omissions": omissions} if omissions else {}),
            **current,
        }
        base_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Baseline écrite : {base_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
