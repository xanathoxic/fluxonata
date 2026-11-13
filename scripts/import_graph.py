#!/usr/bin/env python3
"""Graphe d'imports entre modules du backend (AR0.1, docs/ROADMAP_ARCHITECTURE.md).

Calcule les arêtes « module A importe module B » (imports de tête ET imports locaux à une fonction), hors tests et migrations,
les composantes fortement connexes (cycles), et compare à une base gelée (`backend/tests/fixtures/import_graph_baseline.json`).

    python scripts/import_graph.py            # affiche le graphe et les cycles ; code 1 si la base diffère
    python scripts/import_graph.py --write    # réécrit la base (à justifier dans le commit : nouvelle dépendance, ou dépendance retirée)

Pourquoi gelé et non « aucun cycle » : le graphe actuel est déjà cyclique (runs ↔ workflows, etc.). On interdit d'aggraver, et on
verrouille chaque simplification. Bibliothèque standard seule, aucun accès réseau ni base.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
BASELINE = BACKEND / "tests" / "fixtures" / "import_graph_baseline.json"
SKIP_DIRS = {"migrations", "tests", "__pycache__"}


def modules() -> set[str]:
    return {
        p.name
        for p in BACKEND.iterdir()
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".") and (p / "__init__.py").exists()
    }


def _python_files(module: str):
    for path in (BACKEND / module).rglob("*.py"):
        if not SKIP_DIRS.intersection(path.relative_to(BACKEND / module).parts[:-1]):
            yield path


def edges() -> dict[tuple[str, str], int]:
    """(source, cible) -> nombre d'instructions d'import (information ; seule la présence de l'arête est gelée)."""
    known = modules()
    out: dict[tuple[str, str], int] = {}
    for src in sorted(known):
        for path in _python_files(src):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    targets = [node.module.split(".")[0]]
                for dst in targets:
                    if dst in known and dst != src:
                        out[(src, dst)] = out.get((src, dst), 0) + 1
    return out


def cycles(pairs) -> list[list[str]]:
    """Composantes fortement connexes de plus d'un module (Tarjan)."""
    graph: dict[str, set[str]] = {}
    for a, b in pairs:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set())
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                found.append(sorted(comp))

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return sorted(found)


def file_graph() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Graphe fichier -> fichier (imports absolus ET relatifs), séparé en (imports de tête, imports faits dans une fonction).

    Seuls les imports de tête s'exécutent au chargement : un cycle entre eux serait un vrai cycle d'import (ImportError possible).
    Un import local à une fonction est différé : il ne boucle pas au chargement, mais garde un couplage."""
    files: dict[str, Path] = {}
    for m in modules():
        for path in _python_files(m):
            rel = path.relative_to(BACKEND).with_suffix("")
            files[".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)] = path

    def resolve(name: str) -> str | None:
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            if ".".join(parts[:i]) in files:
                return ".".join(parts[:i])
        return None

    top: dict[str, set[str]] = {}
    deferred: dict[str, set[str]] = {}
    for dotted, path in files.items():
        pkg = dotted if path.name == "__init__.py" else dotted.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        in_function = {
            id(n)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
            for n in ast.walk(fn)
        }
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = pkg.split(".") if pkg else []
                    parts = parts[: len(parts) - (node.level - 1)]
                    base = ".".join([*parts, *([node.module] if node.module else [])])
                names = [base, *(f"{base}.{a.name}" for a in node.names)]
            for name in names:
                target = resolve(name)
                if target and target != dotted:
                    (deferred if id(node) in in_function else top).setdefault(dotted, set()).add(target)
    return top, deferred


def _pairs(graph: dict[str, set[str]]) -> list[tuple[str, str]]:
    return [(a, b) for a, targets in graph.items() for b in targets]


def file_cycles() -> tuple[list[list[str]], list[list[str]]]:
    """(cycles entre fichiers avec les seuls imports de tête, cycles en comptant aussi les imports locaux)."""
    top, deferred = file_graph()
    merged = {k: top.get(k, set()) | deferred.get(k, set()) for k in set(top) | set(deferred)}
    return cycles(_pairs(top)), cycles(_pairs(merged))


def snapshot() -> dict:
    e = edges()
    return {
        "_doc": "Base gelée des dépendances entre modules (scripts/import_graph.py --write). Voir docs/ROADMAP_ARCHITECTURE.md, AR0.1.",
        "modules": sorted(modules()),
        "edges": sorted([a, b] for a, b in e),
        "cycles": cycles(e),
        "cycles_fichiers_imports_de_tete": file_cycles()[0],
        "cycles_fichiers_avec_imports_locaux": file_cycles()[1],
    }


def diff(actual: dict, frozen: dict) -> tuple[list, list]:
    a = {tuple(x) for x in actual["edges"]}
    f = {tuple(x) for x in frozen["edges"]}
    return sorted(a - f), sorted(f - a)


def main(argv: list[str]) -> int:
    snap = snapshot()
    if "--write" in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(
            f"base écrite : {BASELINE.relative_to(ROOT)} ({len(snap['edges'])} dépendances, {len(snap['cycles'])} groupe(s) cyclique(s))"
        )
        return 0
    counts = edges()
    for (a, b), n in sorted(counts.items()):
        print(f"{a:11s} -> {b:11s} {n:3d}")
    print("cycles :", snap["cycles"] or "aucun")
    frozen = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"edges": []}
    added, removed = diff(snap, frozen)
    for a, b in added:
        print(f"NOUVELLE dépendance non prévue : {a} -> {b}")
    for a, b in removed:
        print(f"dépendance retirée (verrouillez le gain avec --write) : {a} -> {b}")
    return 1 if added or removed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
