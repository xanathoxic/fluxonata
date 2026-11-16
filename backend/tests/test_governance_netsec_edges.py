"""Cas limites de la gouvernance des promotions, de la liste des transformations et de la garde réseau."""

import pytest
from rest_framework.exceptions import ValidationError

from core import netsec
from tenancy import governance
from tenancy.models import Environment, Role
from tests.conftest import make_user
from tests.wf_helpers import make_action, node, publish
from workflows import transforms
from workflows.transforms import MAX_GROUPS, MAX_ITEMS, TransformError

# ---- gouvernance


@pytest.fixture
def version(world):
    ws = world["ws_a"]
    make_action(ws, "echo")
    return ws, publish(ws, "g2", [node("a", action="echo", inputs={"value": "x"})], [], user=world["designer"])


def env(ws, kind):
    return Environment.objects.get(workspace=ws, kind=kind)


def test_policy_rules_must_be_booleans(world):
    with pytest.raises(ValidationError) as exc:
        governance.publish_policy(world["ws_a"], {"forbid_self_review": "oui"}, world["designer"])
    assert "booléen" in str(exc.value.detail)


def test_promotion_requests_are_refused_for_the_wrong_environment(version, world):
    ws, v = version
    with pytest.raises(ValidationError, match="Environnement inconnu"):
        governance.request_promotion(v, env(world["ws_b"], "prod"), world["designer"])
    dev = Environment.objects.create(workspace=ws, kind="dev", name="dev")
    with pytest.raises(ValidationError, match="développement"):
        governance.request_promotion(v, dev, world["designer"])


def test_a_version_already_promoted_or_already_waiting_is_not_requested_again(version, world):
    ws, v = version
    designer = world["designer"]
    governance.request_promotion(v, env(ws, "test"), designer)  # test needs no review: approved at once
    with pytest.raises(ValidationError, match="déjà promue"):
        governance.request_promotion(v, env(ws, "test"), designer)
    promo = governance.request_promotion(v, env(ws, "prod"), designer)
    with pytest.raises(ValidationError, match="déjà en attente"):
        governance.request_promotion(v, env(ws, "prod"), designer)
    reviewer = make_user("rita", ws, Role.APPROVER)
    governance.decide_promotion(promo.pk, reviewer, False, "non")
    with pytest.raises(ValidationError, match="déjà été traitée"):
        governance.decide_promotion(promo.pk, reviewer, True)


# ---- transformations


CTX = {"inputs": {"rows": [{"k": 1, "g": "a", "n": 2}, {"k": 2, "g": "b", "n": 3}], "texte": "a,b"}}


def test_oversized_lists_and_texts_are_refused():
    big = {"inputs": {"l": list(range(MAX_ITEMS + 1)), "t": ",".join("x" * (MAX_ITEMS + 1))}}
    with pytest.raises(TransformError, match="Liste trop longue"):
        transforms.apply({"op": "unique", "items": "inputs.l"}, big)
    with pytest.raises(TransformError, match="Trop d'éléments"):
        transforms.apply({"op": "split", "text": "inputs.t"}, big)


def test_groups_and_aggregates_are_bounded_and_checked():
    many = {"inputs": {"l": list(range(MAX_GROUPS + 1))}}
    with pytest.raises(TransformError, match="Trop de groupes"):
        transforms.apply({"op": "group", "items": "inputs.l", "by": "item"}, many)
    with pytest.raises(TransformError, match="Agrégat invalide"):
        transforms.apply(
            {"op": "group", "items": "inputs.rows", "by": "item.g", "aggregates": [{"name": "x", "fn": "nope"}]}, CTX
        )
    out = transforms.apply(
        {
            "op": "group",
            "items": "inputs.rows",
            "by": "item.g",
            "aggregates": [
                {"name": "n_items", "fn": "count"},
                {"name": "premier", "fn": "first", "expr": "item.k"},
                {"name": "tous", "fn": "list", "expr": "item.k"},
                {"name": "total", "fn": "sum", "expr": "item.n"},
            ],
        },
        CTX,
    )["items"]
    assert out[0]["n_items"] == 1 and out[0]["premier"] == 1 and out[0]["tous"] == [1] and out[0]["total"] == 2


def test_merge_refusals_and_unmatched_rows():
    node_ = {"op": "merge", "items": "inputs.rows", "right": "inputs.rows", "left_by": "item.k", "right_by": "item.k"}
    with pytest.raises(TransformError, match="Mode de fusion"):
        transforms.apply({**node_, "merge_mode": "xor"}, CTX)
    with pytest.raises(TransformError, match="clé de chaque côté"):
        transforms.apply({**node_, "left_by": ""}, CTX)
    scalars = {"inputs": {"a": [1, 2], "b": [2]}}
    out = transforms.apply(
        {
            "op": "merge",
            "items": "inputs.a",
            "right": "inputs.b",
            "left_by": "item",
            "right_by": "item",
            "merge_mode": "left",
        },
        scalars,
    )
    assert out["items"] == [{"left": 1, "right": None}, {"left": 2, "right": 2}]


def test_the_validator_lists_the_expressions_of_grouping_and_merging():
    plain, per = transforms.expressions(
        {
            "op": "group",
            "items": "inputs.rows",
            "by": "{{ item.g }}",
            "aggregates": [{"name": "t", "fn": "sum", "expr": "item.n"}, "x"],
        }
    )
    assert plain == ["inputs.rows"] and per == ["item.g", "item.n"]
    plain, per = transforms.expressions(
        {"op": "merge", "items": "inputs.a", "right": "inputs.b", "left_by": "item.id", "right_by": "item.id"}
    )
    assert plain == ["inputs.a", "inputs.b"] and per == ["item.id", "item.id"]


# ---- garde réseau


def test_an_unreadable_address_is_never_allowed_and_sorts_last():
    assert netsec.never_allowed("pas-une-ip") is True
    assert netsec._order("pas-une-ip") == (9, "pas-une-ip")
    assert netsec._order("10.0.0.1")[0] == 4 and netsec._order("::1")[0] == 6


def test_an_invalid_port_is_refused():
    with pytest.raises(netsec.UnsafeURL, match="Port invalide"):
        netsec.validate_url("https://exemple.test:99999/")
