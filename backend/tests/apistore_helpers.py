"""Fixture et aides partagées des tests de l'API store (la fixture est importée dans conftest.py)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apistore import service
from apistore.models import Plan, Product, ProductWorkflow, Subscription
from tenancy.models import Role, ServiceAccount
from tests.test_engine_start import raw_version
from tests.wf_helpers import node

BASE = "/api/v1/plugins/apistore/v1"


@pytest.fixture
def store(db):
    from tests.conftest import make_user, make_workspace  # import tardif : conftest importe ce module

    ws = make_workspace("store")
    owner, asker = make_user("owner", ws, Role.DESIGNER), make_user("asker", ws, Role.DESIGNER)
    raw_version(ws, "hello", [node("j", "join")], [], owner)
    bot_user = get_user_model().objects.create_user("svc.store.bot", password=None)
    bot = ServiceAccount.objects.create(user=bot_user, workspace=ws, name="bot")
    product = Product.objects.create(
        workspace=ws, slug="p1", name="Produit", status=Product.Status.PUBLISHED, run_as=bot
    )
    ProductWorkflow.objects.create(product=product, workflow=ws.workflows.get(slug="hello"))
    auto = Plan.objects.create(product=product, slug="libre", name="Libre", approval="auto", environments=["test"])
    manual = Plan.objects.create(
        product=product, slug="pro", name="Pro", approval="manual", environments=["test", "prod"]
    )
    from tenancy.models import Membership

    Membership.objects.create(user=bot_user, workspace=ws, role=Role.DESIGNER)
    return {"ws": ws, "owner": owner, "asker": asker, "product": product, "auto": auto, "manual": manual, "bot": bot}


def active_key(s, plan=None, **plan_fields):
    plan = plan or s["auto"]
    for k, v in plan_fields.items():
        setattr(plan, k, v)
    plan.save()
    sub = service.subscribe(s["product"], plan, consumer="équipe x", user=s["asker"])
    if sub.status != Subscription.Status.ACTIVE:
        sub = service.decide(sub, s["owner"], approve=True)
    key, raw = service.issue_key(sub, s["owner"])
    return sub, key, raw


def call(raw, body=None, workflow="hello", product="p1", ws="store", **headers):
    c = APIClient()
    return c.post(
        f"{BASE}/{ws}/{product}/workflows/{workflow}/runs/",
        body if body is not None else {"environment": "test", "inputs": {}},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {raw}" if raw else "",
        **headers,
    )
