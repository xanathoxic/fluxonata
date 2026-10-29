import json

import pytest

from ai import service
from ai.evals.cases import DRAFT_CASES, INJECTION_REQUESTS, SEARCH_CASES
from ai.models import AiCall, AiPolicy, AiProvider
from ai.providers import AnthropicProvider, Completion, FakeProvider, OpenAICompatProvider
from audit.models import AuditEvent
from catalog.demo_actions import DEMO_ACTIONS
from catalog.models import Action, ActionVersion
from core import netsec
from tenancy.models import Role
from tests.conftest import client_for, make_user
from tests.wf_helpers import drain, launch, make_action, node, publish, schema
from vault.models import Secret
from workflows.models import Workflow

BASE = "/api/v1/workspaces/team-a"


@pytest.fixture
def ai_ws(world):
    ws = world["ws_a"]
    ts = DEMO_ACTIONS[0]
    Action.objects.filter(workspace=ws, slug="text-stats").update(description=ts["description"], tags=ts["tags"])
    for spec in DEMO_ACTIONS[3:5]:
        a = Action.objects.create(
            workspace=ws, slug=spec["slug"], name=spec["name"], description=spec["description"], tags=spec["tags"]
        )
        ActionVersion.objects.create(
            action=a, version="1.0.0", **{k: spec[k] for k in ("source", "input_schema", "output_schema", "limits")}
        )
    # what the catalog offers are finished workflows, not their building blocks
    for slug, name, description, action in (
        ("statistiques-de-texte", "Statistiques de texte", ts["description"], "text-stats"),
        (
            "analyse-validee",
            "Analyse avec validation",
            "Analyse un texte puis demande une validation s'il est long.",
            "text-stats",
        ),
    ):
        publish(ws, slug, [node("a", action=action, inputs={"text": "x"})], [], user=world["designer"])
        Workflow.objects.filter(workspace=ws, slug=slug).update(name=name, description=description, in_catalog=True)
    admin = make_user("ada", ws, Role.ADMIN)
    AiPolicy.objects.update_or_create(
        workspace=ws, defaults={"mode": "local_strict", "dry_run": False}
    )  # on in its own tests
    AiProvider.objects.create(workspace=ws, name="fake", kind="fake")
    return ws, admin


def cfg(ws, name="fake"):
    return AiProvider.objects.get(workspace=ws, name=name)


def test_off_by_default_nothing_is_sent(world):
    ws = world["ws_a"]
    AiProvider.objects.create(workspace=ws, name="fake", kind="fake")
    with pytest.raises(service.AiRefused) as e:
        service.catalog_search(ws, world["designer"], "x")
    assert e.value.code == "AI_DISABLED" and e.value.status == 403
    r = client_for(world["designer"]).post(f"{BASE}/ai/catalog-search/", {"query": "x"}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "AI_DISABLED"


@pytest.mark.parametrize("case", SEARCH_CASES)
def test_catalog_search_with_fake_provider(ai_ws, world, case):
    ws, _ = ai_ws
    out = service.catalog_search(ws, world["designer"], case["query"], provider_name="fake")
    assert case["expect"] in [m["slug"] for m in out["matches"]]


def test_unknown_slugs_from_the_model_are_discarded(ai_ws, world):
    ws, _ = ai_ws
    prov = FakeProvider(
        script=json.dumps(
            {"matches": [{"slug": "does-not-exist", "reason": "x"}, {"slug": "statistiques-de-texte", "reason": "ok"}]}
        )
    )
    out = service.catalog_search(ws, world["designer"], "q", cfg=cfg(ws), provider=prov)
    assert [m["slug"] for m in out["matches"]] == ["statistiques-de-texte"]


def test_strict_local_mode_forbids_cloud_and_never_falls_back(ai_ws, world):
    ws, _ = ai_ws
    s = Secret(workspace=ws, name="k")
    s.set_value("sk-ant-test-key-123456")
    s.save()
    AiProvider.objects.create(workspace=ws, name="cloud", kind="cloud_anthropic", model="m", secret=s)
    with pytest.raises(service.AiRefused) as e:
        service.catalog_search(ws, world["designer"], "x", provider_name="cloud")
    assert e.value.code == "CLOUD_FORBIDDEN"
    # default selection never picks a cloud provider, and an unavailable local provider does not trigger any external fallback
    AiProvider.objects.filter(name="fake").update(enabled=False)
    with pytest.raises(service.AiRefused) as e2:
        service.catalog_search(ws, world["designer"], "x")
    assert e2.value.code == "PROVIDER_NOT_FOUND"
    assert not AiCall.objects.filter(provider="cloud").exists()


def test_local_server_down_gives_a_clear_error_and_no_other_provider_is_tried(ai_ws, world, settings):
    ws, _ = ai_ws
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    AiProvider.objects.create(workspace=ws, name="llama", kind="local_openai", base_url="http://127.0.0.1:9", model="m")
    with pytest.raises(service.AiRefused) as e:
        service.catalog_search(ws, world["designer"], "x", provider_name="llama")
    assert e.value.code == "PROVIDER_UNREACHABLE"
    assert AiCall.objects.get().status == "error"


def test_cloud_mode_requires_explicit_provider_masks_data_and_logs_no_content(ai_ws, world, monkeypatch):
    ws, _ = ai_ws
    AiPolicy.objects.filter(workspace=ws).update(mode="cloud_allowed")
    s = Secret(workspace=ws, name="k")
    s.set_value("sk-ant-test-key-123456")
    s.save()
    AiProvider.objects.create(workspace=ws, name="cloud", kind="cloud_anthropic", model="some-model", secret=s)
    seen = {}

    def fake_request(method, url, headers=None, body=None, **kw):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return (
            200,
            {},
            json.dumps(
                {
                    "content": [{"type": "text", "text": '{"matches": []}'}],
                    "usage": {"input_tokens": 11, "output_tokens": 3},
                    "model": "some-model",
                }
            ).encode(),
        )

    monkeypatch.setattr(netsec, "safe_request", fake_request)
    service.catalog_search(
        ws, world["designer"], "mail jean@example.org depuis 10.1.2.3 clé ghp_" + "a" * 30, provider_name="cloud"
    )
    body = json.dumps(seen["body"])
    assert (
        seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["anthropic-version"] == "2023-06-01"
    )
    assert seen["headers"]["x-api-key"] == "sk-ant-test-key-123456"
    assert "jean@example.org" not in body and "10.1.2.3" not in body and "ghp_" not in body and "[email]" in body
    call = AiCall.objects.get()
    assert call.kind == "cloud_anthropic" and call.input_tokens == 11 and call.content is None  # metadata only
    assert "jean@example.org" not in json.dumps(call.params)


def test_budget_is_enforced(ai_ws, world):
    ws, _ = ai_ws
    AiPolicy.objects.filter(workspace=ws).update(daily_token_budget=50)
    service.catalog_search(ws, world["designer"], "texte", provider_name="fake")  # consumes > 50 estimated tokens
    with pytest.raises(service.AiRefused) as e:
        service.catalog_search(ws, world["designer"], "texte", provider_name="fake")
    assert e.value.code == "AI_BUDGET_EXCEEDED" and e.value.status == 429


def test_non_json_model_output_is_rejected_and_recorded(ai_ws, world):
    ws, _ = ai_ws
    with pytest.raises(service.AiRefused) as e:
        service.catalog_search(
            ws,
            world["designer"],
            "q",
            cfg=cfg(ws),
            provider=FakeProvider(script="Je suis désolé, voici du texte libre."),
        )
    assert e.value.code == "MODEL_OUTPUT_INVALID" and AiCall.objects.get().status == "invalid"


@pytest.mark.parametrize("case", DRAFT_CASES)
def test_draft_generation_is_a_validated_unpublished_draft(ai_ws, world, case):
    ws, _ = ai_ws
    out = service.draft_workflow(
        ws, world["designer"], case["request"], f"draft-{case['id']}", "Brouillon", provider_name="fake"
    )
    used = {n["action"] for n in out["graph"]["nodes"] if n["type"] == "action"}
    assert set(case["must_use"]) <= used
    assert [i for i in out["issues"] if i["level"] == "error"] == []
    wf = Workflow.objects.get(workspace=ws, slug=f"draft-{case['id']}")
    assert wf.versions.count() == 0 and wf.draft_graph["meta"]["ai_generated"] is True  # never published automatically
    assert AuditEvent.objects.filter(action="ai.draft_workflow").exists()


def test_draft_accepts_inputs_listed_as_bare_properties(ai_ws):
    ws, _ = ai_ws
    raw = {
        "inputs": {"texte": {"type": "string"}},
        "nodes": [
            {"id": "etape_1", "type": "action", "action": "text-stats", "inputs": {"text": "{{ inputs.texte }}"}}
        ],
        "edges": [],
    }
    graph, issues = service.sanitize_draft(raw, ws, "bare-inputs")
    assert graph["inputs"]["properties"] == {"texte": {"type": "string"}}
    assert not [i for i in issues if i["code"] == "unknown_input"]


def test_draft_gets_enough_tokens_whatever_the_provider_cap(ai_ws, world):
    ws, _ = ai_ws
    AiProvider.objects.filter(workspace=ws, name="fake").update(max_tokens=600)
    prov = FakeProvider()
    seen = []
    complete = prov.complete
    prov.complete = lambda system, user, *, max_tokens, timeout: (
        seen.append(max_tokens) or complete(system, user, max_tokens=max_tokens, timeout=timeout)
    )
    cfg = AiProvider.objects.get(workspace=ws, name="fake")
    service.draft_workflow(ws, world["designer"], DRAFT_CASES[0]["request"], "draft-cap", "B", cfg=cfg, provider=prov)
    # the draft, then its check: each call gets at least the room its own prompt needs
    assert seen == [service.PROMPTS[k]["min_tokens"] for k in ("draft_workflow", "review_workflow")]


def test_a_cut_off_answer_is_reported_as_truncated_not_as_garbage(ai_ws, world):
    ws, _ = ai_ws
    cfg = AiProvider.objects.get(workspace=ws, name="fake")
    cfg.max_tokens = 100
    cfg.save()
    cut = FakeProvider(script='{"nodes": [{"id": "etape_1", "type": "act')
    cut.complete = lambda system, user, *, max_tokens, timeout: Completion(
        '{"nodes": [{"id": "e', 10, max_tokens, 1, "x"
    )
    with pytest.raises(service.AiRefused) as e:
        service.run_prompt(
            ws, world["designer"], "catalog_search", data="x", context={"catalog": []}, cfg=cfg, provider=cut
        )
    assert e.value.code == "MODEL_OUTPUT_TRUNCATED"
    assert AiCall.objects.get().error_code == "MODEL_OUTPUT_TRUNCATED"


def test_draft_repairs_what_models_get_wrong_in_a_predictable_way(ai_ws):
    ws, _ = ai_ws
    act = lambda i: {"id": i, "type": "action", "action": "text-stats", "inputs": {"text": "x"}}  # noqa: E731
    raw = {
        "inputs": {"type": "object", "properties": {}},
        "nodes": [
            act("a"),
            {"id": "c", "type": "condition", "expr": "{{ steps.a.output.words }} > 3"},
            act("b"),
            act("d"),
            act("e"),
        ],
        "edges": [
            {"from": "a", "to": "c"},
            {"from": "c", "to": "b", "label": "true"},
            {"from": "c", "to": "d", "label": "false"},
            {"from": "b", "to": "e"},
            {"from": "d", "to": "e"},
        ],
    }
    graph, issues = service.sanitize_draft(raw, ws, "repair")
    cond = next(n for n in graph["nodes"] if n["id"] == "c")
    assert cond["expr"] == "steps.a.output.words > 3"
    join = next(n for n in graph["nodes"] if n["type"] == "join")
    assert {(e["from"], e["to"]) for e in graph["edges"] if e["to"] == join["id"]} == {
        ("b", join["id"]),
        ("d", join["id"]),
    }
    assert {"from": join["id"], "to": "e"} in graph["edges"]
    assert [i for i in issues if i["code"] in ("missing_join", "bad_expr", "invalid_expression")] == []
    assert not [i for i in issues if "Jonction" in i["message"] or "Expression invalide" in i["message"]]


def test_draft_records_the_provider_even_when_none_was_named(ai_ws, world):
    ws, _ = ai_ws
    out = service.draft_workflow(ws, world["designer"], DRAFT_CASES[0]["request"], "draft-default", "Brouillon")
    assert out["graph"]["meta"]["provider"] == "fake"


def test_draft_context_tells_the_model_each_action_output_fields(ai_ws, world):
    ws, _ = ai_ws
    prov = FakeProvider()
    cfg = AiProvider.objects.get(workspace=ws, name="fake")
    service.draft_workflow(ws, world["designer"], DRAFT_CASES[0]["request"], "draft-ctx", "B", cfg=cfg, provider=prov)
    ctx = json.loads(prov.calls[0][1].split("<context>")[1].split("</context>")[0])
    stats = next(a for a in ctx["catalog"] if a["slug"] == "text-stats")
    assert stats["outputs"] and "words" in stats["outputs"]


HOSTILE = [
    {
        "nodes": [{"id": f"n{i}", "type": "action", "action": "text-stats", "inputs": {}} for i in range(500)],
        "edges": [],
    },  # too many
    {"nodes": [{"id": "a", "type": "shell", "title": "x"}], "edges": []},  # forbidden type
    {"nodes": [{"id": "a", "type": "foreach", "workflow": "x"}], "edges": []},
    {"nodes": [{"id": "a", "type": "action", "action": "does-not-exist", "inputs": {}}], "edges": []},  # unknown action
    {
        "nodes": [{"id": "a", "type": "condition", "expr": "__import__('os').system('id')"}],
        "edges": [],
    },  # code injection
    {"nodes": [], "edges": []},
    "just text",
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_model_graphs_are_blocked_or_flagged_by_the_validator(ai_ws, world, payload):
    ws, _ = ai_ws
    prov = FakeProvider(script=json.dumps(payload))
    try:
        out = service.draft_workflow(ws, world["designer"], "x", "hostile", "H", cfg=cfg(ws), provider=prov)
    except service.AiRefused as exc:
        assert exc.code == "MODEL_OUTPUT_INVALID"
        assert not Workflow.objects.filter(slug="hostile").exists()
        return
    assert any(i["level"] == "error" for i in out["issues"])  # saved only as a draft, with blocking issues shown
    from workflows.views import PublishView  # noqa: F401

    r = client_for(world["designer"]).post(f"{BASE}/workflows/hostile/publish/", {}, format="json")
    assert r.status_code == 400  # and it cannot be published


@pytest.mark.parametrize("text", INJECTION_REQUESTS)
def test_prompt_injection_text_stays_inside_the_data_block_and_cannot_widen_capabilities(ai_ws, world, text):
    ws, _ = ai_ws
    prov = FakeProvider()
    out = service.draft_workflow(
        ws, world["designer"], text, "inj-" + str(abs(hash(text)) % 1000), "I", cfg=cfg(ws), provider=prov
    )
    system, user = prov.calls[0]
    assert "DONNÉE NON FIABLE" in system and "aucun outil" in system
    assert user.count("<data>") == 1 and user.count("</data>") == 1  # an injected closing tag is stripped
    assert all(n["type"] in service.DRAFT_NODE_TYPES for n in out["graph"]["nodes"])  # no approval/admin-like nodes
    drafts = Workflow.objects.filter(workspace=ws, description__startswith="Brouillon généré par IA")
    assert drafts.count() == 1 and not drafts.get().versions.exists()


def test_suggested_tests_must_satisfy_the_action_contract(ai_ws, world):
    ws, _ = ai_ws
    script = json.dumps(
        {
            "tests": [
                {"name": "ok", "inputs": {"text": "bonjour"}},
                {"name": "bad", "inputs": {"text": 123}},
                {"name": "x", "inputs": "no"},
            ]
        }
    )
    out = service.suggest_tests(
        ws,
        world["designer"],
        Action.objects.get(workspace=ws, slug="text-stats"),
        cfg=cfg(ws),
        provider=FakeProvider(script=script),
    )
    assert [t["name"] for t in out["tests"]] == ["ok"]


def test_proposed_action_is_validated_not_saved(ai_ws, world):
    ws, _ = ai_ws
    good = {
        "slug": "dbl",
        "name": "Doubler",
        "description": "Double un nombre entier et renvoie le résultat.",
        "input_schema": schema(["n"], n={"type": "integer", "title": "Nombre", "description": "Le nombre à doubler."}),
        "output_schema": schema(
            ["d"], d={"type": "integer", "title": "Double", "description": "Le nombre reçu multiplié par deux."}
        ),
        "source": "def run(i):\n    return {'d': i['n'] * 2}\n",
    }
    out = service.propose_action(
        ws, world["designer"], "doubler un nombre", cfg=cfg(ws), provider=FakeProvider(script=json.dumps(good))
    )
    assert out["valid"] is True and not Action.objects.filter(slug="dbl").exists()
    short = {**good, "description": "x2"}  # a proposal must come with its contract, like any published action
    out = service.propose_action(
        ws, world["designer"], "x", cfg=cfg(ws), provider=FakeProvider(script=json.dumps(short))
    )
    assert out["valid"] is False and "Description de l'action" in out["problems"]["contract"][0]
    bad = {**good, "source": "def run(i:\n"}
    out = service.propose_action(ws, world["designer"], "x", cfg=cfg(ws), provider=FakeProvider(script=json.dumps(bad)))
    assert out["valid"] is False and "source" in out["problems"]


def test_error_explanation_is_schema_validated(ai_ws, world):  # noqa: D103
    ws, _ = ai_ws
    ok = service.explain_error(
        ws, world["designer"], code="TIMEOUT", message="Délai dépassé", cfg=cfg(ws), provider=FakeProvider()
    )
    assert ok["summary"] and ok["next_steps"]
    with pytest.raises(service.AiRefused):
        service.explain_error(
            ws, world["designer"], code="X", message="m", cfg=cfg(ws), provider=FakeProvider(script='{"summary": 5}')
        )


def test_provider_and_policy_administration_is_admin_only_and_guards_urls(ai_ws, world, settings):
    ws, admin = ai_ws
    d = client_for(world["designer"])
    assert d.put(f"{BASE}/ai/policy/", {"mode": "local_strict"}, format="json").status_code == 403
    assert d.post(f"{BASE}/ai/providers/", {"name": "x", "kind": "fake"}, format="json").status_code == 403
    a = client_for(admin)
    bad = a.post(
        f"{BASE}/ai/providers/",
        {"name": "llama", "kind": "local_openai", "base_url": "http://127.0.0.1:8080", "model": "m"},
        format="json",
    )
    assert bad.status_code == 400 and "ORCH_EGRESS_ALLOWLIST" in json.dumps(bad.json(), ensure_ascii=False)
    settings.ORCH_EGRESS_ALLOWLIST = "127.0.0.1"
    assert (
        a.post(
            f"{BASE}/ai/providers/",
            {"name": "llama", "kind": "local_openai", "base_url": "http://127.0.0.1:8080", "model": "m"},
            format="json",
        ).status_code
        == 201
    )
    assert (
        a.post(
            f"{BASE}/ai/providers/", {"name": "c", "kind": "cloud_anthropic", "model": "m"}, format="json"
        ).status_code
        == 400
    )  # needs a key
    caps = a.get(f"{BASE}/ai/providers/fake/capabilities/").json()
    assert caps["allowed_by_policy"] is True and caps["cloud"] is False
    assert a.put(f"{BASE}/ai/policy/", {"mode": "nonsense"}, format="json").status_code == 400
    assert (
        a.put(f"{BASE}/ai/policy/", {"mode": "cloud_allowed", "daily_token_budget": 1000}, format="json").status_code
        == 200
    )
    assert AuditEvent.objects.filter(action="ai.policy").exists()
    usage = a.get(f"{BASE}/ai/usage/").json()
    assert "used_today" in usage and client_for(world["outsider"]).get(f"{BASE}/ai/usage/").status_code == 404


def test_ai_api_endpoints_permissions_and_flow(ai_ws, world):
    ws, _ = ai_ws
    v, d = client_for(world["viewer"]), client_for(world["designer"])
    assert (
        v.post(
            f"{BASE}/ai/catalog-search/", {"query": "statistiques de texte", "provider": "fake"}, format="json"
        ).json()["matches"][0]["slug"]
        == "statistiques-de-texte"
    )
    assert (
        v.post(f"{BASE}/ai/draft-workflow/", {"request": "x", "slug": "a", "name": "A"}, format="json").status_code
        == 403
    )  # needs edit
    r = d.post(
        f"{BASE}/ai/draft-workflow/",
        {"request": "statistiques de texte", "slug": "api-draft", "name": "Via API", "provider": "fake"},
        format="json",
    )
    assert r.status_code == 201 and "Brouillon non publié" in r.json()["notice"]
    assert (
        d.post(
            f"{BASE}/ai/draft-workflow/",
            {"request": "x", "slug": "api-draft", "name": "dup", "provider": "fake"},
            format="json",
        ).status_code
        == 400
    )
    assert (
        d.post(f"{BASE}/actions/text-stats/ai/suggest-tests/", {"provider": "fake"}, format="json").status_code == 200
    )
    assert (
        client_for(world["outsider"]).post(f"{BASE}/ai/catalog-search/", {"query": "x"}, format="json").status_code
        == 404
    )


def test_providers_capabilities_and_wire_formats(monkeypatch):
    assert FakeProvider().capabilities()["cloud"] is False
    assert OpenAICompatProvider("http://x", "m").capabilities()["structured_output"] == "json_mode"
    assert AnthropicProvider("m", "k").capabilities()["cloud"] is True
    sent = {}

    def fake(method, url, headers=None, body=None, **kw):
        sent.update(url=url, body=json.loads(body), headers=headers)
        return 200, {}, json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    monkeypatch.setattr(netsec, "safe_request", fake)
    comp = OpenAICompatProvider("http://llama.local:8080/", "qwen", api_key="tok").complete(
        "s", "u", max_tokens=10, timeout=5
    )
    assert sent["url"] == "http://llama.local:8080/v1/chat/completions" and sent["body"]["response_format"] == {
        "type": "json_object"
    }
    assert (
        sent["body"]["temperature"] == 0 and sent["headers"]["Authorization"] == "Bearer tok" and comp.input_tokens >= 1
    )  # tokens estimated when absent


def test_ai_step_in_a_workflow_is_traceable_and_validated(ai_ws, world, monkeypatch):
    ws, _ = ai_ws
    make_action(ws, "echo")
    out_schema = schema(["label"], label={"type": "string"})
    v = publish(
        ws,
        "with-ai",
        [
            node("a", action="echo", inputs={"value": "bonjour"}),
            node("i", "ai", provider="fake", prompt="Qualifie : {{ steps.a.output.value }}", output_schema=out_schema),
        ],
        [{"from": "a", "to": "i"}],
        outputs={"l": "{{ steps.i.output.label }}"},
        user=world["designer"],
    )
    from ai.runner import AiRunner
    from runs.models import Execution

    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, provider=None: (
            setattr(self, "execution", execution)
            or setattr(self, "provider", FakeProvider(script='{"label": "positif"}'))
        ),
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "succeeded" and run.outputs == {"l": "positif"}
    call = AiCall.objects.get(use_case="ai_step")
    assert call.provider == "fake" and call.prompt_version == "1.0" and call.status == "ok" and call.content is None
    assert Execution.objects.filter(step_run__run=run, action_version__action__slug="__ai_step__").count() == 1


def test_ai_step_with_wrong_schema_output_fails_and_policy_off_blocks(ai_ws, world, monkeypatch):
    ws, _ = ai_ws
    v = publish(
        ws,
        "with-ai2",
        [node("i", "ai", provider="fake", prompt="x", output_schema=schema(["label"], label={"type": "string"}))],
        [],
        user=world["designer"],
    )
    from ai.runner import AiRunner

    monkeypatch.setattr(
        AiRunner,
        "__init__",
        lambda self, execution, provider=None: (
            setattr(self, "execution", execution) or setattr(self, "provider", FakeProvider(script='{"label": 42}'))
        ),
    )
    run = drain(launch(ws, v, world["designer"]))
    assert run.status == "failed" and run.error["code"] == "MODEL_OUTPUT_INVALID"
    AiPolicy.objects.filter(workspace=ws).update(mode="off")
    run2 = drain(launch(ws, v, world["designer"]))
    assert run2.status == "failed" and run2.error["code"] == "AI_DISABLED"


def test_ai_node_validation(ai_ws, world):
    from workflows.validation import validate

    ws, _ = ai_ws
    g = {
        "inputs": schema(),
        "outputs": {},
        "edges": [],
        "nodes": [
            {
                "id": "i",
                "type": "ai",
                "name": "I",
                "provider": "ghost",
                "prompt": "",
                "output_schema": {"type": "string"},
            }
        ],
    }
    codes = {i["code"] for i in validate(g, ws)}
    assert {"missing_prompt", "unknown_provider", "bad_output_schema"} <= codes


def test_openai_compatible_base_url_may_already_end_with_v1():
    assert OpenAICompatProvider("http://h:11434/v1/", "m").base_url == "http://h:11434"
    assert OpenAICompatProvider("http://h:11434", "m").base_url == "http://h:11434"


def test_a_script_written_by_a_model_is_kept_flagged_and_blocks_publication(ai_ws):
    ws, _ = ai_ws
    raw = {
        "inputs": {"texte": {"type": "string"}},
        "nodes": [
            {
                "id": "etape_1",
                "type": "script",
                "name": "Compter",
                "language": "python",
                "script": "nb = len(texte.split())\nSystem.log(nb)",
                "bindings": {"texte": "{{ inputs.texte }}"},
                "outputs": [{"name": "nb", "type": "integer"}],
                "timeout_seconds": 99999,
                "evil": "x",
            }
        ],
        "edges": [],
    }
    graph, issues = service.sanitize_draft(raw, ws, "scripted")
    node = graph["nodes"][0]
    assert node["ai_review"] is True and node["timeout_seconds"] == 60 and "evil" not in node
    assert node["outputs"] == [{"name": "nb", "type": "integer"}]
    assert [i["code"] for i in issues if i["level"] == "error"] == ["ai_review_required"]  # nothing else wrong with it
    node["ai_review"] = False  # what « J'ai relu ce code » does
    from workflows.validation import validate_graph

    assert [i for i in validate_graph(graph, ws, "scripted") if i["level"] == "error"] == []


def test_a_model_script_with_a_syntax_error_is_reported_like_a_human_one(ai_ws):
    ws, _ = ai_ws
    raw = {"nodes": [{"id": "a", "type": "script", "script": "def (:", "outputs": []}], "edges": []}
    _, issues = service.sanitize_draft(raw, ws, "broken")
    assert {"ai_review_required", "script_syntax"} <= {i["code"] for i in issues}


def test_a_draft_may_use_every_step_type_and_the_validator_still_has_the_last_word(ai_ws, world):
    ws, _ = ai_ws
    raw = {
        "inputs": {"type": "object", "properties": {"liste": {"type": "array"}}, "required": ["liste"]},
        "nodes": [
            {"id": "a", "type": "approval", "name": "", "title": "Valider", "allow_self_approval": True},
            {"id": "w", "type": "wait", "seconds": 5},
            {"id": "l", "type": "foreach", "workflow": "n-existe-pas", "items": "{{ inputs.liste }}"},
            {"id": "e", "type": "throw", "message": "stop"},
        ],
        "edges": [
            {"from": "a", "to": "w"},
            {"from": "w", "to": "l"},
            {"from": "l", "to": "e", "label": "error"},
            {"from": "l", "to": "zzz"},
        ],
        "variables": [{"name": "essais", "type": "integer", "value": 0}],
    }
    graph, issues = service.sanitize_draft(raw, ws, "tous-types")
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["a"]["name"] == "Approbation" and "allow_self_approval" not in nodes["a"]
    assert nodes["l"]["items"] == "inputs.liste" and "max_iterations" not in nodes["l"]
    assert len(graph["edges"]) == 3 and graph["variables"] == [{"name": "essais", "type": "integer", "value": 0}]
    codes = {i["code"] for i in issues}
    assert "unknown_workflow" in codes  # nothing guessed: the person is told


def test_a_long_request_is_given_whole_to_the_model_or_refused_never_cut_silently(ai_ws, world):
    ws, _ = ai_ws
    long_request = "étape " * 400 + "FIN-DE-LA-DEMANDE"  # ≈ 2400 characters: well past the old 1500 cut
    prov = FakeProvider()
    service.draft_workflow(ws, world["designer"], long_request, "demande-longue", "L", cfg=cfg(ws), provider=prov)
    first_user_message = prov.calls[0][1]
    assert "FIN-DE-LA-DEMANDE" in first_user_message
    too_long = "x" * (service.MAX_REQUEST + 1)
    r = client_for(world["designer"]).post(
        f"{BASE}/ai/draft-workflow/",
        {"request": too_long, "name": "N", "slug": "trop-long", "provider": "fake"},
        format="json",
    )
    assert r.status_code == 400 and "trop longue" in json.dumps(r.json(), ensure_ascii=False)


def test_the_generation_benchmark_runs_and_its_checks_recognise_a_correct_graph(ai_ws, world):
    from io import StringIO

    from django.core.management import call_command

    from ai.evals.cases import WORKFLOW_CASES

    good = {
        "inputs": {"type": "object", "properties": {"valeurs": {"type": "array"}, "plafond": {"type": "number"}}},
        "variables": [],
        "notes": [],
        "nodes": [
            {
                "id": "s",
                "type": "script",
                "outputs": [{"name": "moyenne", "type": "number"}, {"name": "maximum", "type": "number"}],
            },
            {"id": "c", "type": "condition", "expr": "steps.s.output.moyenne > inputs.plafond"},
            {"id": "e", "type": "throw", "message": "Moyenne trop élevée"},
            {"id": "w", "type": "wait", "seconds": 30},
            {"id": "a", "type": "approval", "title": "Valider la moyenne"},
        ],
        "edges": [],
    }
    case = next(c for c in WORKFLOW_CASES if c["id"] == "moyenne")
    assert all(fn(good) for _, fn in case["checks"])
    off_by_one = {
        **good,
        "nodes": [dict(n, expr=n["expr"].replace(">", ">=")) if n["id"] == "c" else n for n in good["nodes"]],
    }
    assert [label for label, fn in case["checks"] if not fn(off_by_one)] == ["décision moyenne > plafond (pas >=)"]
    out = StringIO()
    call_command("ai_eval", "team-a", "--provider", "fake", "--workflows", "--case", "moyenne", stdout=out)
    assert "[moyenne #1]" in out.getvalue() and "Total :" in out.getvalue()


def test_run_diagnosis_gives_facts_without_output_values(world):
    from tests.wf_helpers import edge

    ws, user = world["ws_a"], world["designer"]
    make_action(ws, "echo")
    make_action(ws, "boom")
    v = publish(
        ws,
        "diag",
        [node("s1", action="echo", inputs={"value": "secret-value-42"}), node("s2", action="boom")],
        [edge("s1", "s2")],
        user=user,
    )
    run = drain(launch(ws, v, user))
    assert run.status == "failed"
    run.definition["graph"]["nodes"][1]["inputs"] = {
        "x": "{{ steps.s1.output.absent }}",
        "y": "{{ steps.s9.output.v }}",
    }
    diag = service.run_diagnosis(run)
    assert diag["failed"]["step"] == "S2" and "boom" in diag["failed"]["message"]
    assert [t["status"] for t in diag["timeline"]] == ["succeeded", "failed"]
    problems = {r["reads"]: r["problem"] for r in diag["references"]}
    assert "champs présents : value" in problems["steps.s1.output.absent"]
    assert "pas été exécutée" in problems["steps.s9.output.v"]
    assert "secret-value-42" not in json.dumps(diag)
