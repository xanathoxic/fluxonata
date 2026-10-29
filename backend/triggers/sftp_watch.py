"""Watching an SFTP folder: new (or replaced) files become items of a polling trigger. Listing only: nothing is read or moved."""

from runs import sftp_node
from runs.sftp_node import SftpStepError


def list_files(t):
    node = {
        "connection": t.request.get("connection"),
        "operation": "list",
        "path": t.request.get("path", "/"),
        "mode": "reel",
    }
    try:
        spec = sftp_node.prepare(node, lambda v, _ctx: v, {}, t.workspace_id, t.environment_id)
    except SftpStepError as exc:
        return None, exc.message
    result, error = sftp_node.execute_spec(spec, t.workspace, t.environment_id, t)
    if error:
        return None, error["message"]
    prefix = spec["path"].rstrip("/")
    needle = str(t.request.get("name_contains") or "").lower()
    items = [
        {
            "id": f"{e['name']}|{e['size']}|{int(e['modified'] or 0)}",
            "name": e["name"],
            "path": f"{prefix}/{e['name']}",
            "size": e["size"],
            "modified": e["modified"],
        }
        for e in result["entries"]
        if not e["is_dir"] and needle in e["name"].lower()
    ]
    return items, None
