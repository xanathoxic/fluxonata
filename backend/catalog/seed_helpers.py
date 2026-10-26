"""Shared by the demo seeders: install the demo actions, publish a workflow, lay a graph out."""

from collections import defaultdict

from catalog.demo_actions import DEMO_ACTIONS
from catalog.models import Action, ActionVersion

DEMO_FOLDERS = {
    "text-stats": "Démonstration/Texte et données",
    "json-transform": "Démonstration/Texte et données",
    "make-report": "Démonstration/Texte et données",
    "http-test-service": "Démonstration/Connecteurs",
    "send-notification": "Démonstration/Connecteurs",
    "slow-wait": "Démonstration/Connecteurs",
}


def install_demo_actions(ws, owner) -> None:
    """Idempotent: the demo actions (and their folders) in a workspace. Never overrides a folder somebody chose."""
    for spec in DEMO_ACTIONS:
        action, _ = Action.objects.get_or_create(
            workspace=ws,
            slug=spec["slug"],
            defaults={k: spec[k] for k in ("name", "description", "domain", "tags")} | {"owner": owner},
        )
        if not action.folder and spec["slug"] in DEMO_FOLDERS:
            action.folder = DEMO_FOLDERS[spec["slug"]]
            action.save(update_fields=["folder"])
        ActionVersion.objects.get_or_create(
            action=action,
            version=spec["version"],
            defaults={k: spec[k] for k in ("source", "input_schema", "output_schema", "limits", "examples")}
            | {k: spec[k] for k in ("side_effects", "idempotent", "network_policy") if k in spec}
            | {"created_by": owner},
        )


def publish_workflow(
    ws, owner, slug, name, folder, description, graph, changelog="Version de démonstration", in_catalog=False
):
    """Idempotent: create the workflow with `graph` as its draft and publish 1.0.0 (references pinned to exact versions)."""
    from workflows.models import Workflow, WorkflowVersion
    from workflows.validation import pin_graph

    wf, _ = Workflow.objects.get_or_create(
        workspace=ws,
        slug=slug,
        defaults={"name": name, "owner": owner, "draft_graph": graph, "description": description, "folder": folder},
    )
    if not wf.folder:
        wf.folder = folder
        wf.save(update_fields=["folder"])
    if not wf.versions.exists():
        WorkflowVersion.objects.create(
            workflow=wf,
            version="1.0.0",
            graph=pin_graph(graph, ws),
            input_schema=graph["inputs"],
            created_by=owner,
            changelog=changelog,
        )
        if (
            in_catalog
        ):  # offered in « Catalogue d'automatisations » (only the first time: later choices are the admins')
            wf.in_catalog = True
            wf.save(update_fields=["in_catalog"])
    return wf


def layout(nodes: list[dict], edges: list[dict], dx: int = 160, dy: int = 118) -> None:
    """Left-to-right layered layout (column = longest path from the entries, rows centred): sets node["ui"]."""
    preds, ids = defaultdict(list), [n["id"] for n in nodes]
    for e in edges:
        preds[e["to"]].append(e["from"])
    depth: dict[str, int] = {}

    def d(i):
        if i not in depth:
            depth[i] = 1 + max((d(p) for p in preds[i]), default=-1)
        return depth[i]

    cols = defaultdict(list)
    for i in ids:
        cols[d(i)].append(i)
    height = max(len(c) for c in cols.values())
    for col, members in cols.items():
        for row, nid in enumerate(members):
            next(n for n in nodes if n["id"] == nid)["ui"] = {
                "x": col * dx,
                "y": int((row - (len(members) - 1) / 2 + (height - 1) / 2) * dy),
            }
