from tests.conftest import client_for

BASE = "/api/v1/workspaces/team-a"


def field(key, type="text", **extra):
    return {"key": key, "label": key.capitalize(), "type": type, **extra}


def definition(*fields):
    return {"version": 1, "sections": [{"id": "s1", "title": "Demande", "fields": list(fields)}]}


def make_form(user, slug, d, *, publish=True, base=BASE, **extra):
    """A library form (draft = `d`), published as version 1.0.0 unless told otherwise. Returns the client used."""
    c = client_for(user)
    r = c.post(
        f"{base}/form-library/", {"slug": slug, "name": slug.capitalize(), "definition": d, **extra}, format="json"
    )
    assert r.status_code == 201, r.data
    if publish:
        p = c.post(f"{base}/form-library/{slug}/publish/", {}, format="json")
        assert p.status_code == 201, p.data
    return c
