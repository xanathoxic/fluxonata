import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditEvent
from runs import engine
from tenancy import governance
from tenancy.models import Environment, Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import edge, launch, make_action, node, publish


def make_manager(ws):
    return make_user("manu", ws, Role.SPACE_MANAGER)


@pytest.fixture
def wf(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    v = publish(
        ws,
        "gov",
        [node("a", action="echo", inputs={"value": "x"}), node("ap", "approval", title="ok")],
        [edge("a", "ap")],
        user=world["designer"],
    )
    return ws, v


def prod(ws):
    return Environment.objects.get(workspace=ws, kind="prod")


def test_prod_run_requires_promotion_then_review(wf, world):
    ws, v = wf
    designer = world["designer"]
    with pytest.raises(PermissionDenied, match="promue en production"):
        launch(ws, v, designer, env="prod")
    reviewer = make_user("rita", ws, Role.APPROVER)
    promo = governance.request_promotion(v, prod(ws), designer, "prêt")
    assert promo.status == "requested"
    with pytest.raises(PermissionDenied):  # still not promoted
        launch(ws, v, designer, env="prod")
    with pytest.raises(PermissionDenied):  # a designer has no approve permission at all
        governance.decide_promotion(promo.pk, designer, True)
    governance.decide_promotion(promo.pk, reviewer, True, "ok")
    run = launch(ws, v, designer, env="prod")
    gov = run.definition["governance"]
    assert gov["decision"] == "allowed" and gov["promotion_id"] == promo.pk and gov["policy_version"] == 0
    assert AuditEvent.objects.filter(action="promotion.decide", workspace=ws).exists()


def test_manager_cannot_review_own_promotion_but_can_review_others(wf, world):
    ws, v = wf
    mgr = make_manager(ws)
    promo = governance.request_promotion(v, prod(ws), mgr)
    with pytest.raises(PermissionDenied, match="Séparation"):
        governance.decide_promotion(promo.pk, mgr, True)
    other_mgr = make_user("mona", ws, Role.SPACE_MANAGER)
    assert governance.decide_promotion(promo.pk, other_mgr, True).status == "approved"


def test_reviewer_needs_approve_permission_and_viewer_cannot_request(wf, world):
    ws, v = wf
    promo = governance.request_promotion(v, prod(ws), world["designer"])
    with pytest.raises(PermissionDenied):
        governance.decide_promotion(promo.pk, world["viewer"], True)
    with pytest.raises(PermissionDenied):
        governance.request_promotion(v, prod(ws), world["viewer"])


def test_policy_is_versioned_and_recorded_with_each_run(wf, world):
    ws, v = wf
    admin = make_user("ada", ws, Role.ADMIN)
    pv = governance.publish_policy(ws, {"promotion_requires_review": False}, admin)
    assert pv.version == 1 and pv.rules["prod_requires_promotion"] is True
    promo = governance.request_promotion(v, prod(ws), world["designer"])
    assert promo.status == "approved" and promo.policy_version_id == pv.id  # auto-approved by policy v1
    run = launch(ws, v, world["designer"], env="prod")
    assert run.definition["governance"]["policy_version"] == 1
    pv2 = governance.publish_policy(ws, {"prod_requires_promotion": False}, admin)
    assert pv2.version == 2
    with pytest.raises(PermissionError):
        pv.rules = {}
        pv.save()
    with pytest.raises(ValidationError):
        governance.publish_policy(ws, {"nonsense": True}, admin)


def test_no_self_approval_of_human_step_in_prod(wf, world):
    ws, v = wf
    admin = make_user("ada", ws, Role.ADMIN)
    governance.publish_policy(ws, {"promotion_requires_review": False}, admin)
    governance.request_promotion(v, prod(ws), admin)
    from runs.models import Approval
    from tests.wf_helpers import drain

    run = drain(launch(ws, v, admin, env="prod"), until=lambda r: Approval.objects.exists())
    ap = Approval.objects.get()
    with pytest.raises(PermissionDenied, match="propre demande"):
        engine.decide_approval(ap.pk, admin, True)  # admin has the approve permission but is the requester
    other = make_user("zoe", ws, Role.APPROVER)
    assert engine.decide_approval(ap.pk, other, True).status == "approved"
    assert run


def test_policy_api_permissions(wf, world):
    ws, _ = wf
    c = client_for(world["designer"])
    assert c.get("/api/v1/workspaces/team-a/policy/").json()["rules"]["prod_requires_promotion"] is True
    assert c.put("/api/v1/workspaces/team-a/policy/", {"rules": {}}, format="json").status_code == 403
    admin = make_user("ada", ws, Role.ADMIN)
    r = client_for(admin).put(
        "/api/v1/workspaces/team-a/policy/", {"rules": {"forbid_self_review": False}}, format="json"
    )
    assert r.status_code == 200 and r.json()["version"] == 1
    assert client_for(world["outsider"]).get("/api/v1/workspaces/team-a/policy/").status_code == 404


def test_promotion_api_flow_and_isolation(wf, world):
    ws, v = wf
    designer = client_for(world["designer"])
    base = "/api/v1/workspaces/team-a"
    r = designer.post(f"{base}/workflows/gov/versions/{v.version}/promote/", {"environment": "prod"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "requested" and r.json()["can_decide"] is False
    pid = r.json()["id"]
    assert (
        designer.post(
            f"{base}/workflows/gov/versions/{v.version}/promote/", {"environment": "prod"}, format="json"
        ).status_code
        == 400
    )
    reviewer = make_user("rita", ws, Role.APPROVER)
    rc = client_for(reviewer)
    assert rc.get(f"{base}/promotions/?status=requested").json()["results"][0]["can_decide"] is True
    assert rc.post(f"{base}/promotions/{pid}/decide/", {"approve": True}, format="json").json()["status"] == "approved"
    assert client_for(world["outsider"]).get(f"{base}/promotions/").status_code == 404
    assert (
        designer.post(f"{base}/workflows/gov/runs/", {"environment": "prod", "inputs": {}}, format="json").status_code
        == 202
    )
