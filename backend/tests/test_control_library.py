from tests.conftest import client_for
from tests.form_helpers import BASE, definition, make_form
from tests.test_form_controls import ADDRESS, BOXES, LINES, custom

URL = f"{BASE}/control-library/"


def create(c, slug="adresse", spec=ADDRESS, **extra):
    return c.post(URL, {"slug": slug, "name": slug.capitalize(), "spec": spec, **extra}, format="json")


def test_create_edit_publish_and_list_published(world):
    c = client_for(world["designer"])
    r = create(c, icon="home", tags=["ui"])
    assert r.status_code == 201 and r.data["mode"] == "group" and r.data["latest_version"] is None
    assert c.get(f"{URL}?published=1").data["results"] == []  # nothing published yet
    d = c.get(f"{URL}adresse/").data
    assert d["spec"] == ADDRESS and d["usage"] == [] and d["draft_revision"] == 1
    p = c.patch(f"{URL}adresse/", {"spec": {**ADDRESS, "parts": [*ADDRESS["parts"]][:2]}, "revision": 1}, format="json")
    assert p.status_code == 200 and p.data["draft_revision"] == 2
    stale = c.patch(f"{URL}adresse/", {"spec": ADDRESS, "revision": 1}, format="json")
    assert stale.status_code == 409
    pub = c.post(f"{URL}adresse/publish/", {"changelog": "première"}, format="json")
    assert pub.status_code == 201 and pub.data["version"] == "1.0.0"
    assert c.post(f"{URL}adresse/publish/", {"bump": "patch"}, format="json").data["version"] == "1.0.1"
    listed = c.get(f"{URL}?published=1").data["results"]
    assert listed[0]["published"]["version"] == "1.0.1" and listed[0]["published"]["icon"] == "home"
    assert listed[0]["published"]["spec"]["parts"] == p.data["spec"]["parts"]
    v = c.get(f"{URL}adresse/versions/1.0.0/")
    assert v.status_code == 200 and v.data["spec"]["mode"] == "group"
    assert c.get(f"{URL}adresse/versions/9.9.9/").status_code == 404


def test_invalid_specs_are_refused_everywhere_and_check_explains(world):
    c = client_for(world["designer"])
    bad = {"version": 1, "mode": "group", "parts": [{"key": "a", "label": "A", "type": "datagrid"}]}
    assert create(c, spec=bad).status_code == 400
    assert (
        create(c, "brouillon", spec={"version": 1, "mode": "repeat", "parts": []}).status_code == 201
    )  # a draft may be empty
    create(c)
    assert c.patch(f"{URL}adresse/", {"spec": bad, "revision": 1}, format="json").status_code == 400
    r = c.post(
        f"{URL}check/",
        {"spec": {"version": 1, "mode": "repeat", "parts": [{"key": "a", "label": "A", "type": "datagrid"}]}},
        format="json",
    )
    assert r.status_code == 200 and "spec.parts.0" in r.data["problems"]
    assert c.post(f"{URL}check/", {"spec": ADDRESS}, format="json").data["problems"] == {}
    # an empty draft cannot be published
    assert c.post(f"{URL}brouillon/publish/", {}, format="json").status_code == 400
    c.post(URL, {"slug": "vide", "name": "Vide"}, format="json")
    assert c.post(f"{URL}vide/publish/", {}, format="json").status_code == 400


def test_usage_lists_forms_using_the_control_and_deleting_keeps_their_copy(world):
    c = client_for(world["designer"])
    create(c, "lignes", LINES)
    make_form(
        world["designer"],
        "commande",
        definition(
            custom("l", LINES, label="Lignes")
            | {"control": {"ref": "lignes", "version": "1.0.0", "name": "Lignes", "spec": LINES}}
        ),
        publish=False,
    )
    assert c.get(f"{URL}lignes/").data["usage"] == ["commande"]
    assert c.delete(f"{URL}lignes/").status_code == 204
    assert (
        c.get(f"{BASE}/form-library/commande/").data["definition"]["sections"][0]["fields"][0]["control"]["spec"]
        == LINES
    )


def test_permissions_and_duplicates(world):
    assert create(client_for(world["designer"]), "a").status_code == 201
    assert create(client_for(world["designer"]), "a").status_code == 400  # the identifier exists
    reader = client_for(world["viewer"])
    assert reader.get(URL).status_code == 200
    assert create(reader, "b").status_code == 403
    assert reader.patch(f"{URL}a/", {"name": "x"}, format="json").status_code == 403
    assert reader.post(f"{URL}a/publish/", {}, format="json").status_code == 403
    assert reader.delete(f"{URL}a/").status_code == 403
    assert client_for(world["outsider"]).get(URL).status_code in (403, 404)


def test_a_published_form_pins_the_copy_of_its_control(world):
    c = client_for(world["designer"])
    d = definition(custom("choix", BOXES))
    make_form(world["designer"], "avec-controle", d)
    v = c.get(f"{BASE}/form-library/avec-controle/versions/1.0.0/").data
    assert v["definition"]["sections"][0]["fields"][0]["control"]["spec"]["mode"] == "items"


def test_a_sample_reply_shows_what_a_forms_reading_of_a_service_would_give_without_any_call(world):
    c = client_for(world["designer"])
    src = {
        "connection": "svc",
        "path": "/b",
        "items_path": "rows",
        "value_path": "id",
        "label_path": "n",
        "group_path": "g",
    }
    spec = {"version": 1, "mode": "items", "dynamic": True}
    sample = {"rows": [{"id": "a", "n": "A", "g": "G"}, {"id": "b", "n": "B"}], "sel": ["a"]}
    url = f"{URL}source-preview/"
    ask = lambda **k: c.post(url, {"spec": spec, "sample": sample, **k}, format="json")  # noqa: E731
    r = ask(kind="options", config=src)
    assert r.status_code == 200 and r.data["options"] == [
        {"value": "a", "label": "A", "group": "G"},
        {"value": "b", "label": "B"},
    ]
    assert ask(kind="options", config=src, sample={"autre": 1}).data["problem"]
    pre = {"connection": "svc", "path": "/p", "value_path": "sel"}
    ok = ask(kind="prefill", config=pre).data
    assert ok["value"] == ["a"] and ok["problem"] == ""
    assert ask(kind="prefill", config=pre, sample={"sel": "zz"}).data["problem"] == "Une liste est attendue."
    # a control that does not receive options, or a reading that is not valid, is explained
    assert ask(kind="options", config=src, spec={"version": 1, "mode": "group", "parts": []}).data["problems"]
    assert ask(kind="options", config={"connection": "", "path": "x"}).data["problems"]
    assert (
        client_for(world["viewer"])
        .post(url, {"kind": "options", "spec": spec, "config": src, "sample": sample}, format="json")
        .status_code
        == 403
    )


def test_the_type_of_a_control_cannot_change(world):
    c = client_for(world["designer"])
    create(c)
    same = {"version": 1, "mode": "group", "parts": []}
    assert c.patch(f"{URL}adresse/", {"spec": same, "revision": 1}, format="json").status_code == 200
    other = {"version": 1, "mode": "repeat", "parts": []}
    r = c.patch(f"{URL}adresse/", {"spec": other, "revision": 2}, format="json")
    assert r.status_code == 400 and "type" in str(r.data).lower()


def test_options_carry_the_values_of_the_fields_they_open(world):
    c = client_for(world["designer"])
    spec = {
        "version": 1,
        "mode": "choice",
        "dynamic": True,
        "parts": [
            {"key": "nom", "label": "Nom", "type": "text"},
            {"key": "prenom", "label": "Prénom", "type": "text"},
            {"key": "total", "label": "Total", "type": "number", "compute": "1+1"},
            {"key": "t", "label": "T", "type": "heading"},
        ],
    }
    cfg = {"connection": "essai", "path": "/", "value_path": "value", "label_path": "label"}
    sample = [
        {"value": "option-1", "nom": "Exemple", "prenom": "Exemple", "total": 5, "autre": "x"},
        {"value": "option-2", "nom": "N" * 600, "prenom": 7},
    ]
    r = c.post(
        f"{URL}source-preview/", {"kind": "options", "spec": spec, "config": cfg, "sample": sample}, format="json"
    )
    assert r.status_code == 200
    first, second = r.data["options"]
    assert first["data"] == {"nom": "Exemple", "prenom": "Exemple"}  # calculated and unknown keys are left out
    assert second["data"] == {"prenom": 7}  # an over-long text is not carried


def test_controls_live_in_a_folder_tree_like_forms(world):
    c = client_for(world["designer"])
    r = create(c, "adresse", folder=" Adresses / Clients ")
    assert r.status_code == 201 and r.data["folder"] == "Adresses/Clients"
    assert create(c, "plain").data["folder"] == ""
    f = f"{BASE}/folders/"
    assert c.post(f, {"kind": "control", "path": "Vide"}, format="json").status_code in (200, 201)
    moved = c.patch(f"{URL}plain/", {"folder": "Adresses"}, format="json")
    assert moved.status_code == 200 and moved.data["folder"] == "Adresses"
    ren = c.patch(f, {"kind": "control", "path": "Adresses", "new_path": "Postal"}, format="json")
    assert ren.status_code == 200
    assert (
        c.get(f"{URL}plain/").data["folder"] == "Postal" and c.get(f"{URL}adresse/").data["folder"] == "Postal/Clients"
    )
    # a reader cannot create or move
    reader = client_for(world["viewer"])
    assert reader.post(f, {"kind": "control", "path": "X"}, format="json").status_code == 403


def test_list_reports_how_many_forms_use_each_control(world):
    c = client_for(world["designer"])
    create(c, "adresse")
    create(c, "libre")
    assert {r["slug"]: r["used_by"] for r in c.get(URL).data["results"]} == {"adresse": 0, "libre": 0}
    for slug in ("f1", "f2"):
        make_form(
            world["designer"],
            slug,
            definition(
                custom("a", ADDRESS, label="A")
                | {"control": {"ref": "adresse", "version": "1.0.0", "name": "A", "spec": ADDRESS}}
            ),
            publish=False,
        )
    assert {r["slug"]: r["used_by"] for r in c.get(URL).data["results"]} == {"adresse": 2, "libre": 0}
