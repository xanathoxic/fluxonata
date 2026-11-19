"""API de gestion de l'API store : droits par rôle, parcours demande → approbation → clé, usage, révocation."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apistore import service
from apistore.models import Plan, Product, ProductWorkflow
from tenancy.auth import hash_token, new_token
from tenancy.models import ApiToken, Role
from tests.apistore_helpers import call
from tests.conftest import client_for, make_user
from tests.test_engine_start import raw_version
from tests.wf_helpers import node

M = "/api/v1/plugins/apistore/manage/store"


@pytest.fixture
def people(store):
    ws = store["ws"]
    return {
        "admin": make_user("boss", ws, Role.ADMIN),
        "approver": make_user("appr", ws, Role.APPROVER),
        "operator": make_user("oper", ws, Role.OPERATOR),
        "viewer": make_user("vue", ws, Role.VIEWER),
    }


def test_only_an_administrator_creates_products_and_plans(store, people):
    body = {"slug": "p2", "name": "Deux"}
    for who in ("viewer", "operator", "approver"):
        assert client_for(people[who]).post(f"{M}/products/", body, format="json").status_code == 403
    r = client_for(people["admin"]).post(f"{M}/products/", body, format="json")
    assert r.status_code == 201 and r.json()["status"] == "draft"
    assert client_for(people["admin"]).post(f"{M}/products/", body, format="json").status_code == 409
    plan = {"slug": "b", "name": "B", "environments": ["test"], "per_minute": 5, "approval": "auto"}
    assert client_for(people["operator"]).post(f"{M}/products/p2/plans/", plan, format="json").status_code == 403
    assert client_for(people["admin"]).post(f"{M}/products/p2/plans/", plan, format="json").status_code == 201
    bad = client_for(people["admin"]).post(
        f"{M}/products/p2/plans/", {**plan, "slug": "c", "environments": ["lune"]}, format="json"
    )
    assert bad.status_code == 400


def test_a_product_is_published_only_when_complete_and_drafts_stay_hidden_from_others(store, people):
    admin = client_for(people["admin"])
    admin.post(f"{M}/products/", {"slug": "p2", "name": "Deux"}, format="json")
    assert admin.patch(f"{M}/products/p2/", {"status": "published"}, format="json").status_code == 409
    admin.post(f"{M}/products/p2/plans/", {"slug": "b", "name": "B", "environments": ["test"]}, format="json")
    r = admin.patch(
        f"{M}/products/p2/", {"run_as": "bot", "workflows": ["hello"], "status": "published"}, format="json"
    )
    assert r.status_code == 200 and r.json()["status"] == "published" and r.json()["run_as"] == "bot"
    assert admin.patch(f"{M}/products/p2/", {"run_as": "inconnu"}, format="json").status_code == 400
    assert admin.patch(f"{M}/products/p2/", {"workflows": ["nope"]}, format="json").status_code == 400
    Product.objects.filter(slug="p1").update(status="draft")
    seen = {p["slug"] for p in client_for(people["viewer"]).get(f"{M}/products/").json()}
    assert seen == {"p2"} and "run_as" not in client_for(people["viewer"]).get(f"{M}/products/p2/").json()
    assert client_for(people["viewer"]).get(f"{M}/products/p1/").status_code == 404


def test_the_full_journey_request_approve_key_call_usage_revoke(store, people):
    requester = client_for(people["operator"])
    r = requester.post(f"{M}/subscriptions/", {"product": "p1", "plan": "pro", "consumer": "équipe y"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "pending"
    sub_id = r.json()["id"]
    no_key = requester.post(f"{M}/subscriptions/{sub_id}/keys/", {}, format="json")
    assert no_key.status_code == 409  # pas encore active
    assert requester.post(f"{M}/subscriptions/{sub_id}/decision/", {"approve": True}, format="json").status_code == 403
    approver = client_for(people["approver"])
    assert (
        approver.post(f"{M}/subscriptions/{sub_id}/decision/", {"approve": True}, format="json").json()["status"]
        == "active"
    )
    issued = requester.post(f"{M}/subscriptions/{sub_id}/keys/", {"days": 30}, format="json")
    assert issued.status_code == 201
    raw = issued.json()["key"]
    assert call(raw).status_code == 202
    listing = requester.get(f"{M}/subscriptions/").json()
    assert "'key':" not in str(listing) and raw not in str(listing)  # la valeur n'est montrée qu'à l'émission
    usage = requester.get(f"{M}/subscriptions/{sub_id}/usage/").json()
    assert usage["last_24h"] == {"accepted": 1} and usage["recent"][0]["workflow"] == "hello"
    key_id = issued.json()["id"]
    assert requester.post(f"{M}/subscriptions/{sub_id}/keys/{key_id}/revoke/").status_code == 200
    assert call(raw).status_code == 401


def test_subscribing_needs_a_published_product_and_a_known_plan(store, people):
    c = client_for(people["operator"])
    for body in ({"product": "p1", "plan": "inconnu"}, {"product": "nope", "plan": "libre"}):
        assert c.post(f"{M}/subscriptions/", {**body, "consumer": "x"}, format="json").status_code == 404
    assert (
        client_for(people["viewer"])
        .post(f"{M}/subscriptions/", {"product": "p1", "plan": "libre", "consumer": "x"}, format="json")
        .status_code
        == 403
    )


def test_a_member_sees_and_manages_only_his_own_subscriptions_and_keys(store, people):
    mine = service.subscribe(store["product"], store["auto"], consumer="a", user=people["operator"])
    other = make_user("oper2", store["ws"], Role.OPERATOR)
    theirs = service.subscribe(store["product"], store["auto"], consumer="b", user=other)
    c = client_for(people["operator"])
    assert [s["id"] for s in c.get(f"{M}/subscriptions/").json()] == [mine.pk]
    assert c.get(f"{M}/subscriptions/{theirs.pk}/usage/").status_code == 404
    assert c.post(f"{M}/subscriptions/{theirs.pk}/keys/", {}, format="json").status_code == 404
    assert [s["id"] for s in client_for(people["admin"]).get(f"{M}/subscriptions/").json()] == [theirs.pk, mine.pk]


def test_only_an_administrator_suspends_or_revokes(store, people):
    sub = service.subscribe(store["product"], store["auto"], consumer="a", user=people["operator"])
    url = f"{M}/subscriptions/{sub.pk}/status/"
    assert client_for(people["operator"]).post(url, {"status": "suspended"}, format="json").status_code == 403
    assert client_for(people["admin"]).post(url, {"status": "suspended"}, format="json").json()["status"] == "suspended"
    assert client_for(people["admin"]).post(url, {"status": "pending"}, format="json").status_code == 400


def test_an_api_token_can_neither_issue_keys_nor_decide(store, people):
    sub = service.subscribe(store["product"], store["manual"], consumer="a", user=people["operator"])
    raw, prefix, digest = new_token()
    ApiToken.objects.create(
        workspace=store["ws"],
        user=people["admin"],
        name="t",
        prefix=prefix,
        token_hash=digest,
        scopes=["view", "run"],
        expires_at=timezone.now() + timedelta(days=1),
    )
    from rest_framework.test import APIClient

    c = APIClient()
    h = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    assert c.post(f"{M}/subscriptions/{sub.pk}/decision/", {"approve": True}, format="json", **h).status_code == 403
    assert c.post(f"{M}/subscriptions/{sub.pk}/keys/", {}, format="json", **h).status_code == 403
    assert hash_token(raw) == digest


def test_another_workspace_cannot_reach_the_store_data(store, people):
    from tests.conftest import make_workspace

    make_workspace("elsewhere")
    stranger = make_user("zed", None)
    assert client_for(stranger).get("/api/v1/plugins/apistore/manage/store/products/").status_code == 404
    assert client_for(people["admin"]).get("/api/v1/plugins/apistore/manage/elsewhere/products/").status_code == 404


def test_management_does_not_touch_plans_of_a_product_without_workflows(store):
    assert ProductWorkflow.objects.count() == 1 and Plan.objects.count() == 2
    raw_version(store["ws"], "autre", [node("j", "join")], [], store["owner"])


def test_the_packaged_ui_module_exists_and_has_no_html_injection(store):
    from pathlib import Path

    import apistore

    source = (Path(apistore.__file__).parent / "ui" / "index.js").read_text(encoding="utf-8")
    assert "export default function mount(" in source
    # Le texte venu du serveur passe par textContent / createTextNode, jamais par du HTML.
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in source, banned


def test_the_rights_endpoint_tells_the_interface_what_the_person_may_do(store):
    from tests.conftest import client_for

    url = "/api/v1/plugins/apistore/manage/store/me/"
    assert client_for(store["owner"]).get(url).json() == {"administer": False, "approve": False, "run": True}
    assert client_for(store["asker"]).get(url).json()["administer"] is False
    from django.contrib.auth import get_user_model

    assert client_for(get_user_model().objects.create_user("dehors", password="x")).get(url).status_code in (403, 404)


def test_the_options_list_workflows_and_accounts_for_administrators_only(store):
    from tenancy.models import Membership, Role
    from tests.conftest import client_for

    url = "/api/v1/plugins/apistore/manage/store/options/"
    assert client_for(store["owner"]).get(url).status_code == 403  # concepteur : pas administrateur
    admin = Membership.objects.get(user=store["owner"], workspace=store["ws"])
    admin.role = Role.ADMIN
    admin.save()
    body = client_for(store["owner"]).get(url).json()
    assert body["accounts"] == ["bot"]
    assert {"slug": "hello", "name": "hello", "published": True}.items() <= next(
        w for w in body["workflows"] if w["slug"] == "hello"
    ).items()


def test_a_refusal_carries_the_platform_error_shape_so_the_interface_can_show_the_reason(store):
    from apistore.models import ProductWorkflow
    from tenancy.models import Membership, Role
    from tests.conftest import client_for

    Membership.objects.filter(user=store["owner"], workspace=store["ws"]).update(role=Role.ADMIN)
    store["product"].status = "draft"
    store["product"].save()
    ProductWorkflow.objects.filter(product=store["product"]).delete()
    r = client_for(store["owner"]).patch(
        "/api/v1/plugins/apistore/manage/store/products/p1/", {"status": "published"}, format="json"
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "invalid" and "au moins un workflow" in err["message"]
    assert r.json()["detail"] == err["message"]  # forme historique conservée


def test_one_patch_can_link_workflows_and_publish_atomically(store):
    from apistore.models import ProductWorkflow
    from tenancy.models import Membership, Role
    from tests.conftest import client_for

    Membership.objects.filter(user=store["owner"], workspace=store["ws"]).update(role=Role.ADMIN)
    store["product"].status = "draft"
    store["product"].save()
    ProductWorkflow.objects.filter(product=store["product"]).delete()
    url = "/api/v1/plugins/apistore/manage/store/products/p1/"
    c = client_for(store["owner"])
    ok = c.patch(url, {"workflows": ["hello"], "run_as": "bot", "status": "published"}, format="json")
    assert ok.status_code == 200 and ok.json()["status"] == "published" and ok.json()["workflows"] == ["hello"]


def test_an_archived_subscription_can_be_reactivated_or_deleted_by_a_person_who_administers(store):
    from apistore.models import Subscription, UsageEvent
    from tenancy.models import Membership, Role
    from tests.apistore_helpers import active_key, call
    from tests.conftest import client_for

    sub, _, raw = active_key(store)
    assert call(raw).status_code == 202
    Membership.objects.filter(user=store["owner"], workspace=store["ws"]).update(role=Role.ADMIN)
    base = f"/api/v1/plugins/apistore/manage/store/subscriptions/{sub.pk}/"
    c = client_for(store["owner"])
    assert c.delete(base).status_code == 409  # active : pas d'archive, pas de suppression
    assert c.post(base + "status/", {"status": "revoked"}, format="json").status_code == 200
    assert c.post(base + "status/", {"status": "active"}, format="json").json()["status"] == "active"
    c.post(base + "status/", {"status": "revoked"}, format="json")
    assert client_for(store["asker"]).delete(base).status_code in (403, 404)  # pas administrateur
    assert c.delete(base).status_code == 204
    assert not Subscription.objects.filter(pk=sub.pk).exists()
    assert UsageEvent.objects.filter(
        subscription__isnull=True, outcome="accepted"
    ).exists()  # l'historique d'usage reste
    assert c.delete(base).status_code == 404


def test_a_key_can_have_a_chosen_lifetime_and_only_an_administrator_may_make_it_unlimited(store):
    from datetime import timedelta

    from django.utils import timezone

    from apistore.models import ApiKey
    from tenancy.models import Membership, Role
    from tests.apistore_helpers import active_key, call
    from tests.conftest import client_for

    sub, _, _ = active_key(store)
    url = f"/api/v1/plugins/apistore/manage/store/subscriptions/{sub.pk}/keys/"
    owner = client_for(store["asker"])  # le demandeur : concepteur, pas administrateur
    short = owner.post(url, {"days": 7}, format="json").json()
    soon = ApiKey.objects.get(pk=short["id"]).expires_at
    assert timezone.now() + timedelta(days=6) < soon < timezone.now() + timedelta(days=8)
    refused = owner.post(url, {"unlimited": True}, format="json")
    assert refused.status_code == 403 and "administrateur" in refused.json()["error"]["message"]
    Membership.objects.filter(user=store["asker"], workspace=store["ws"]).update(role=Role.ADMIN)
    made = owner.post(url, {"unlimited": True}, format="json")
    assert made.status_code == 201 and made.json()["expires_at"] is None
    assert call(made.json()["key"]).status_code == 202  # une clé sans expiration authentifie
    assert owner.post(url, {"days": 400}, format="json").status_code == 400
