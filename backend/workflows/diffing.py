"""Comparison of two workflow graphs (two published versions, or a version and the draft): what a reviewer needs to see."""

import json

IGNORED = {"ui"}  # layout is not a change of behaviour


def _node_changes(a: dict, b: dict) -> list[dict]:
    out = []
    for key in sorted((set(a) | set(b)) - IGNORED):
        if a.get(key) != b.get(key):
            out.append({"field": key, "before": _short(a.get(key)), "after": _short(b.get(key))})
    return out


def _short(v, limit=600):
    text = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return v if len(text) <= limit else {"tronqué": text[:limit]}


def diff_graphs(a: dict, b: dict) -> dict:
    na = {n["id"]: n for n in a.get("nodes", [])}
    nb = {n["id"]: n for n in b.get("nodes", [])}
    added = [{"id": i, "name": nb[i].get("name", i), "type": nb[i]["type"]} for i in nb if i not in na]
    removed = [{"id": i, "name": na[i].get("name", i), "type": na[i]["type"]} for i in na if i not in nb]
    changed = []
    for i in na:
        if i in nb:
            fields = _node_changes(na[i], nb[i])
            if fields:
                changed.append({"id": i, "name": nb[i].get("name", i), "type": nb[i]["type"], "fields": fields})
    ea = {(e["from"], e["to"], e.get("label")) for e in a.get("edges", [])}
    eb = {(e["from"], e["to"], e.get("label")) for e in b.get("edges", [])}
    edge = lambda t: {"from": t[0], "to": t[1], "label": t[2]}  # noqa: E731
    sections = {}
    for key in ("inputs", "outputs", "variables", "form"):
        if a.get(key) != b.get(key):
            sections[key] = {"before": _short(a.get(key)), "after": _short(b.get(key))}
    return {
        "nodes_added": added,
        "nodes_removed": removed,
        "nodes_changed": changed,
        "edges_added": [edge(t) for t in sorted(eb - ea, key=str)],
        "edges_removed": [edge(t) for t in sorted(ea - eb, key=str)],
        "sections": sections,
        "identical": not (added or removed or changed or eb - ea or ea - eb or sections),
    }
