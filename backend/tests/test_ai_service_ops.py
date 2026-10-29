"""The assistant through its service: edits by operations, generation by plan (fake model, scripted answers)."""

import pytest

from ai import service
from ai.providers import FakeProvider
from tests.test_ai import ai_ws, cfg  # noqa: F401  (fixture)
from tests.test_ai_edit import COVERED, Scripted, blocking, other_steps, run_edit, script_node
from tests.test_ai_edit import graph as app_graph

CALC = {
    "type": "script",
    "id": "calc",
    "name": "Calcul",
    "language": "python",
    "script": "n = len(mots)",
    "bindings": {"mots": "{{ steps.a.output.words }}"},
    "outputs": [{"name": "n", "type": "integer"}],
}


def test_the_assistant_edits_with_operations_and_the_summary_is_what_they_did(ai_ws, world):
    ws, _ = ai_ws
    model = Scripted(
        edit_workflow={"reply": "J'ai ajouté un calcul.", "ops": [{"op": "add_step", "step": CALC, "after": "a"}]},
        review_workflow=COVERED,
    )
    out = run_edit(ws, world["designer"], model)
    assert {n["id"] for n in out["graph"]["nodes"]} == {"a", "calc", "w"}
    assert {(e["from"], e["to"]) for e in out["graph"]["edges"]} == {("a", "calc"), ("calc", "w")}
    assert any("Étape ajoutée : « Calcul »" in c for c in out["changes"]) and blocking(out) == []
    # the model never had to repeat the rest of the workflow: ids, positions and fields of other steps are untouched
    assert out["graph"]["nodes"][0]["ui"] == {"x": 10, "y": 20} and out["graph"]["nodes"][0]["version"] == "1.0.0"


def test_a_question_has_no_operations_and_nothing_to_apply(ai_ws, world):
    ws, _ = ai_ws
    out = run_edit(ws, world["designer"], Scripted(edit_workflow={"reply": "Il compte des mots.", "ops": []}))
    assert out["graph"] is None and out["reply"] == "Il compte des mots."


def test_refused_operations_are_sent_back_once_then_said_plainly(ai_ws, world):
    ws, _ = ai_ws
    bad = {"reply": "J'ai supprimé l'étape.", "ops": [{"op": "remove_step", "id": "inconnue"}]}
    model = Scripted(edit_workflow=bad)
    out = run_edit(ws, world["designer"], model)
    assert model.tasks == ["edit_workflow", "edit_workflow"]  # one more try, with the reason
    assert "n'existe pas" in model.users[1] and "refusées" in model.users[1]
    assert out["graph"] is None and out["reply"].startswith("Je n'ai pas pu appliquer cette modification")


def test_operations_applied_in_part_say_what_was_not_done(ai_ws, world):
    ws, _ = ai_ws
    ops = [{"op": "add_step", "step": CALC, "after": "a"}, {"op": "remove_step", "id": "inconnue"}]
    out = run_edit(
        ws,
        world["designer"],
        Scripted(edit_workflow={"reply": "J'ai ajouté le calcul.", "ops": ops}, review_workflow=COVERED),
    )
    assert out["graph"] is not None and "Non appliqué" in out["reply"] and "n'existe pas" in out["reply"]
    assert (
        "J'ai ajouté le calcul" not in out["reply"] and "en partie" in out["reply"]
    )  # the claim is not repeated on top of a refusal


def test_notes_and_variables_are_deleted_by_operations_and_an_approval_cannot_be(ai_ws, world):
    ws, _ = ai_ws
    g = app_graph()
    g["nodes"].append({"id": "ok", "type": "approval", "name": "Feu vert", "title": "t"})
    g["edges"].append({"from": "w", "to": "ok"})
    ops = [
        {"op": "remove_note", "id": "note_1"},
        {"op": "remove_variable", "name": "v"},
        {"op": "remove_step", "id": "ok"},
    ]
    out = run_edit(ws, world["designer"], Scripted(edit_workflow={"reply": "Fait.", "ops": ops}), g=g)
    assert out["graph"]["notes"] == [] and out["graph"]["variables"] == []
    assert "ok" in {n["id"] for n in out["graph"]["nodes"]} and "Une approbation ne peut pas" in out["reply"]


def test_the_repair_round_also_speaks_in_operations(ai_ws, world):
    ws, _ = ai_ws
    broken = {"reply": "ok", "ops": [{"op": "add_step", "step": script_node("n = len(zzz)", nid="calc"), "after": "a"}]}
    fix = {
        "reply": "corrigé",
        "ops": [
            {
                "op": "set_fields",
                "id": "calc",
                "fields": {"script": "n = len(mots)", "bindings": {"mots": "{{ steps.a.output.words }}"}},
            }
        ],
    }
    model = Scripted(edit_workflow=broken, repair_workflow=fix, review_workflow=COVERED)
    out = run_edit(ws, world["designer"], model)
    assert model.tasks == ["edit_workflow", "repair_workflow", "review_workflow"] and blocking(out) == []
    assert next(n for n in out["graph"]["nodes"] if n["id"] == "calc")["script"] == "n = len(mots)"


# ------------------------------------------------------------------------------------------ generation by plan
PLAN = {
    "inputs": {"texte": {"type": "string"}},
    "notes": ["à relire"],
    "steps": [
        {
            "id": "stats",
            "type": "action",
            "name": "Stats",
            "action": "text-stats",
            "inputs": {"text": "{{ inputs.texte }}"},
        },
        {
            "id": "long",
            "type": "if",
            "name": "Long ?",
            "expr": "steps.stats.output.words > 5",
            "then": [
                {
                    "id": "ok",
                    "type": "approval",
                    "name": "Accord",
                    "title": "Valider",
                    "on_error": [{"id": "ko", "type": "throw", "message": "Refusé"}],
                }
            ],
        },
        {"id": "fin", "type": "wait", "name": "Pause", "seconds": 2},
    ],
}


def draft(ws, user, model):
    return service.build_draft(ws, user, "une demande", "x", cfg=cfg(ws), provider=FakeProvider(script=model))


def test_a_plan_becomes_a_correct_graph_without_any_correction_round(ai_ws, world):
    ws, _ = ai_ws
    model = Scripted(draft_workflow=PLAN, review_workflow=COVERED)
    graph, issues, check, _ = draft(ws, world["designer"], model)
    assert model.tasks == [
        "draft_workflow",
        "review_workflow",
    ]  # nothing to repair: the topology is right by construction
    edges = {(e["from"], e["to"], e.get("label")) for e in graph["edges"]}
    assert {("stats", "long", None), ("long", "ok", "true"), ("ok", "ko", "error")} <= edges
    assert [n["id"] for n in graph["nodes"] if n["id"] not in {e["to"] for e in graph["edges"]}] == ["stats"]
    assert graph["notes"][0]["text"] == "à relire" and [i for i in issues if i["level"] == "error"] == []


def test_a_plan_the_compiler_refuses_is_sent_back_once_with_the_reason(ai_ws, world):
    ws, _ = ai_ws
    bad = {"steps": [{"type": "throw", "id": "t", "message": "x"}, {"type": "wait", "id": "w"}]}
    model = Scripted(draft_workflow=[bad, PLAN], review_workflow=COVERED)
    graph, *_ = draft(ws, world["designer"], model)
    assert model.tasks[:2] == ["draft_workflow", "draft_workflow"] and "inaccessible" in model.users[1]
    assert "stats" in {n["id"] for n in graph["nodes"]}
    with pytest.raises(service.AiRefused, match="plan proposé est inexploitable"):
        draft(ws, world["designer"], Scripted(draft_workflow=bad))


def test_a_raw_graph_answer_is_still_accepted(ai_ws, world):
    ws, _ = ai_ws
    raw = {"inputs": {"texte": {"type": "string"}}, "nodes": [{"id": "a", "type": "wait", "seconds": 3}], "edges": []}
    graph, *_ = draft(ws, world["designer"], Scripted(draft_workflow=raw, review_workflow=COVERED))
    assert [n["id"] for n in graph["nodes"]] == ["a"]


def test_every_answer_says_what_it_spent(ai_ws, world):
    ws, _ = ai_ws
    out = run_edit(ws, world["designer"], Scripted(edit_workflow={"reply": "Il compte.", "ops": []}))
    assert set(out["usage"]) == {"tokens", "today", "budget"} and out["usage"]["tokens"] > 0
    assert out["usage"]["today"] >= out["usage"]["tokens"] and out["usage"]["budget"] == 1_000_000  # the new default


def test_a_small_precise_edit_is_not_read_again_by_a_model_but_a_big_one_is(ai_ws, world):
    ws, _ = ai_ws
    small = Scripted(
        edit_workflow={"reply": "Fait.", "ops": [{"op": "set_fields", "id": "w", "fields": {"seconds": 9}}]}
    )
    out = run_edit(ws, world["designer"], small)
    assert small.tasks == ["edit_workflow"] and out["graph"] is not None and "Contrôle automatique" in out["reply"]
    big = Scripted(
        edit_workflow={"reply": "Fait.", "ops": [{"op": "add_step", "step": CALC, "after": "a"}]},
        review_workflow=COVERED,
    )
    run_edit(ws, world["designer"], big)
    assert big.tasks == ["edit_workflow", "review_workflow"]


def test_a_plan_with_a_calculation_gets_one_retry_then_goes_on_to_the_correction_round(ai_ws, world):
    ws, _ = ai_ws
    plan = {
        "steps": [
            {"id": "stats", "type": "action", "name": "Stats", "action": "text-stats", "inputs": {"text": "x"}},
            {
                "id": "d",
                "type": "if",
                "name": "D",
                "expr": "sqrt(steps.stats.output.words) > 1",
                "then": [{"id": "n", "type": "wait", "seconds": 5}],
            },
        ]
    }
    fix = {
        "reply": "corrigé",
        "ops": [{"op": "set_fields", "id": "d", "fields": {"expr": "steps.stats.output.words > 4"}}],
    }
    model = Scripted(draft_workflow=plan, repair_workflow=fix, review_workflow=COVERED)
    graph, issues, *_ = draft(ws, world["designer"], model)
    assert model.tasks[:3] == ["draft_workflow", "draft_workflow", "repair_workflow"] and "sqrt(" in model.users[1]
    assert next(n for n in graph["nodes"] if n["id"] == "d")["expr"] == "steps.stats.output.words > 4"


def test_a_script_binding_named_like_a_reserved_root_is_dropped(ai_ws, world):
    ws, _ = ai_ws
    step = {**CALC, "bindings": {"steps": '{"a": 1}', "mots": "{{ steps.a.output.words }}", "bad name": "x"}}
    out = run_edit(
        ws,
        world["designer"],
        Scripted(
            edit_workflow={"reply": "ok", "ops": [{"op": "add_step", "step": step, "after": "a"}]},
            review_workflow=COVERED,
        ),
    )
    assert next(n for n in out["graph"]["nodes"] if n["id"] == "calc")["bindings"] == {
        "mots": "{{ steps.a.output.words }}"
    }


def test_a_script_still_to_be_read_is_not_reported_as_a_failed_correction(ai_ws, world):
    ws, _ = ai_ws
    g = app_graph()
    g["nodes"].append(script_node("n = len(zzz)", nid="ancien"))  # shows an error the person must act on
    g["nodes"][-1]["ai_review"] = True
    g["edges"].append({"from": "w", "to": "ancien"})
    ops = [{"op": "set_fields", "id": "w", "fields": {"seconds": 9}}]
    out = run_edit(
        ws,
        world["designer"],
        Scripted(edit_workflow={"reply": "J'ai modifié l'attente.", "ops": ops}),
        "passe la pause à 9 s",
        g=g,
    )
    assert "Je n'ai pas réussi" not in out["reply"]


# ----------------------------------------------------------------------------------------------- the dry run in the pipeline
PLAN_CALC = {
    "inputs": {"lignes": {"type": "array"}, "n": {"type": "integer"}},
    "steps": [
        {
            "id": "calc",
            "type": "script",
            "name": "Total",
            "language": "python",
            "script": "total = sum(l['quantite'] * l['prix'] for l in lignes)",
            "bindings": {"lignes": "{{ inputs.lignes }}"},
            "outputs": [{"name": "total", "type": "number"}],
        },
        {
            "id": "gros",
            "type": "if",
            "name": "Gros ?",
            "expr": "steps.calc.output.total > 10",
            "then": [{"id": "w", "type": "wait", "seconds": 1}],
        },
    ],
}
SAMPLES = {
    "samples": [
        {
            "label": "réaliste",
            "inputs": {"lignes": [{"quantite": 2, "prix": 3.5}], "n": 1},
            "expected": [{"step": "calc", "field": "total", "value": 7.0}],
        }
    ]
}


@pytest.fixture
def dry(ai_ws):
    from ai.models import AiPolicy

    AiPolicy.objects.filter(workspace=ai_ws[0]).update(dry_run=True)
    return ai_ws


def test_the_dry_run_finds_a_script_that_crashes_and_the_correction_round_fixes_it(dry, world):
    ws, _ = dry
    broken = {
        **PLAN_CALC,
        "steps": [
            {**PLAN_CALC["steps"][0], "script": "total = sum(l['qte'] * l['prix'] for l in lignes)"},
            PLAN_CALC["steps"][1],
        ],
    }
    fix = {
        "reply": "corrigé",
        "ops": [{"op": "set_fields", "id": "calc", "fields": {"script": PLAN_CALC["steps"][0]["script"]}}],
    }
    model = Scripted(draft_workflow=broken, dryrun_samples=SAMPLES, repair_workflow=fix, review_workflow=COVERED)
    graph, issues, check, _ = draft(ws, world["designer"], model)
    assert model.tasks.index("dryrun_samples") < model.tasks.index(
        "repair_workflow"
    )  # essay first, then the correction
    rows = model.data(model.tasks.index("repair_workflow"))["issues"]
    assert (
        [r["code"] for r in rows] == ["dryrun_script_error"]
        and "KeyError" in rows[0]["message"]
        and "ligne 1" in rows[0]["message"]
    )
    assert next(n for n in graph["nodes"] if n["id"] == "calc")["script"] == PLAN_CALC["steps"][0]["script"]
    assert check["dryrun"]["ran"] and not [
        i for i in issues if i["code"].startswith("dryrun_") and i["level"] == "error"
    ]
    assert "essai à blanc" in service.check_sentence(check)


def test_the_samples_come_from_the_model_then_the_schema_and_a_wrong_one_is_dropped(dry, world):
    ws, _ = dry
    bad = {"samples": [{"label": "faux", "inputs": {"n": "pas un entier"}}, SAMPLES["samples"][0]]}
    model = Scripted(draft_workflow=PLAN_CALC, dryrun_samples=bad, review_workflow=COVERED)
    _, _, check, _ = draft(ws, world["designer"], model)
    labels = [s["label"] for s in check["dryrun"]["scenarios"]]
    assert labels[0] == "réaliste" and "typique" in labels and "faux" not in labels  # invalid dropped, mechanical kept


def test_the_results_the_request_states_are_compared_and_a_difference_is_a_warning(dry, world):
    ws, _ = dry
    wrong = {"samples": [{**SAMPLES["samples"][0], "expected": [{"step": "calc", "field": "total", "value": 99}]}]}
    model = Scripted(draft_workflow=PLAN_CALC, dryrun_samples=wrong, review_workflow=COVERED)
    _, issues, check, _ = draft(ws, world["designer"], model)
    hit = [i for i in issues if i["code"] == "dryrun_unexpected"]
    assert hit and hit[0]["level"] == "warning" and "attendu 99, obtenu 7" in hit[0]["message"]
    assert "repair_workflow" not in model.tasks  # a warning to a person, not a reason to rewrite


def test_the_dry_run_can_be_turned_off_by_the_workspace_policy(ai_ws, world):
    ws, _ = ai_ws  # off in this fixture, as in the policy a workspace admin can set
    model = Scripted(draft_workflow=PLAN_CALC, review_workflow=COVERED)
    _, _, check, _ = draft(ws, world["designer"], model)
    assert "dryrun_samples" not in model.tasks and check["dryrun"] is None


def test_a_small_edit_does_not_trigger_a_dry_run(dry, world):
    ws, _ = dry
    model = Scripted(edit_workflow={"reply": "ok", "ops": [{"op": "set_fields", "id": "w", "fields": {"seconds": 9}}]})
    run_edit(ws, world["designer"], model)
    assert "dryrun_samples" not in model.tasks


def test_a_quoted_exception_must_be_an_exception_step_not_just_a_word_elsewhere():
    from ai.speclint import spec_lint

    request = "Si elle est refusée, une exception « Machine non conforme »."
    g = {
        "nodes": [
            {"id": "a", "type": "approval", "title": "Escalade : machine non conforme"},
            {"id": "t", "type": "throw", "message": "Autre"},
        ]
    }
    assert [f["message"] for f in spec_lint(request, g, {})] and "Machine non conforme" in spec_lint(request, g, {})[0][
        "message"
    ]
    g["nodes"][1]["message"] = "Machine non conforme"
    assert spec_lint(request, g, {})  # present, but not on an error exit: asked « si elle est refusée »
    g["edges"] = [{"from": "a", "to": "t", "label": "error"}]
    assert spec_lint(request, g, {}) == []


def test_a_refusal_chain_wired_on_the_normal_exit_is_moved_to_the_error_exit(ai_ws, world):
    ws, _ = ai_ws
    nodes = [
        {"id": "ap", "type": "approval", "name": "Ap", "title": "t"},
        {"id": "ok", "type": "wait", "name": "OK", "seconds": 1},
        {"id": "prevenir", "type": "wait", "name": "Prévenir", "seconds": 1},
        {"id": "ko", "type": "throw", "name": "KO", "message": "Refusé"},
    ]
    # the model hangs « notify, then exception » on the plain exit, next to the continuation
    edges = [{"from": "ap", "to": "ok"}, {"from": "ap", "to": "prevenir"}, {"from": "prevenir", "to": "ko"}]
    out = other_steps(ws, world["designer"], nodes, edges)
    got = {(e["from"], e["to"], e.get("label")) for e in out["graph"]["edges"]}
    assert ("ap", "prevenir", "error") in got and ("ap", "ok", None) in got  # the refusal chain is on the error exit
    # a chain that continues elsewhere is a real branch and stays where it is
    edges = [{"from": "ap", "to": "ok"}, {"from": "ap", "to": "prevenir"}]
    out = other_steps(ws, world["designer"], nodes[:3], edges)
    assert {(e["from"], e["to"], e.get("label")) for e in out["graph"]["edges"]} >= {("ap", "prevenir", None)}


def test_an_exception_asked_for_a_refusal_must_hang_on_an_error_exit():
    from ai.speclint import spec_lint

    request = "Si elle est refusée, une exception « Création refusée »."
    nodes = [
        {"id": "ap", "type": "approval", "title": "t"},
        {"id": "t", "type": "throw", "name": "Création refusée", "message": "Création refusée"},
        {"id": "s", "type": "wait"},
    ]
    elsewhere = {"nodes": nodes, "edges": [{"from": "ap", "to": "s"}, {"from": "s", "to": "t"}]}
    assert spec_lint(request, elsewhere, {}) and "add_branch" in spec_lint(request, elsewhere, {})[0]["message"]
    on_error = {"nodes": nodes, "edges": [{"from": "ap", "to": "s"}, {"from": "ap", "to": "t", "label": "error"}]}
    assert spec_lint(request, on_error, {}) == []


def test_a_refusal_exception_hung_on_another_steps_error_exit_does_not_count():
    from ai.speclint import spec_lint

    request = "Si elle est refusée, une exception « Création refusée »."
    nodes = [
        {"id": "ap", "type": "approval", "title": "t"},
        {"id": "act", "type": "action"},
        {"id": "t", "type": "throw", "name": "Création refusée", "message": "Création refusée"},
    ]
    wrong = {"nodes": nodes, "edges": [{"from": "ap", "to": "act"}, {"from": "act", "to": "t", "label": "error"}]}
    assert spec_lint(request, wrong, {})  # it hangs on the action, not on the approval
    right = {"nodes": nodes, "edges": [{"from": "ap", "to": "act"}, {"from": "ap", "to": "t", "label": "error"}]}
    assert spec_lint(request, right, {}) == []


def test_the_refusal_exception_must_hang_on_the_approval_the_request_names():
    from ai.speclint import spec_lint

    request = "Une approbation « Autoriser la création » ; si elle est refusée, une exception « Création refusée »."
    nodes = [
        {"id": "a1", "type": "approval", "title": "Autoriser la création"},
        {"id": "a2", "type": "approval", "title": "Escalade"},
        {"id": "t", "type": "throw", "name": "Création refusée", "message": "Création refusée"},
    ]
    wrong = {"nodes": nodes, "edges": [{"from": "a2", "to": "t", "label": "error"}]}
    assert spec_lint(request, wrong, {})  # it hangs on the other approval
    right = {"nodes": nodes, "edges": [{"from": "a1", "to": "t", "label": "error"}]}
    assert spec_lint(request, right, {}) == []


def test_a_failure_exception_must_hang_on_the_named_action_not_on_another_step():
    from ai.speclint import spec_lint

    ctx = {"catalog": [{"slug": "http-test-service", "name": "Appel HTTP (service de test local)"}]}
    request = (
        "Appel de l'action « Appel HTTP (service de test local) ». Si elle échoue : une exception « Création échouée »."
    )
    nodes = [
        {"id": "h", "type": "action", "action": "http-test-service"},
        {"id": "c", "type": "script"},
        {"id": "t", "type": "throw", "name": "Création échouée", "message": "Création échouée"},
    ]
    wrong = {
        "nodes": nodes,
        "edges": [{"from": "h", "to": "c"}, {"from": "c", "to": "t", "label": "error"}],
    }  # on the script
    assert spec_lint(request, wrong, ctx)
    right = {"nodes": nodes, "edges": [{"from": "h", "to": "c"}, {"from": "h", "to": "t", "label": "error"}]}
    assert spec_lint(request, right, ctx) == []


def test_a_failure_branch_reading_the_failed_steps_output_gets_a_fixed_text_instead(ai_ws, world):
    from ai import service as svc

    nodes = [
        {"id": "appel", "type": "action", "name": "Appel de création", "action": "text-stats", "inputs": {"text": "x"}},
        {
            "id": "analyse",
            "type": "action",
            "name": "Analyse",
            "action": "text-stats",
            "inputs": {"text": "Échec : {{ steps.appel.output.characters | json_encode }} et {{ inputs.texte }}"},
        },
        {"id": "ko", "type": "throw", "name": "KO", "message": "x"},
        {"id": "suite", "type": "wait", "name": "S", "seconds": 1},
    ]
    edges = [
        {"from": "appel", "to": "analyse", "label": "error"},
        {"from": "analyse", "to": "ko"},
        {"from": "appel", "to": "suite"},
    ]
    fixed, count = svc._fix_failed_reads({"nodes": nodes, "edges": edges})
    assert (
        count == 1
        and fixed["nodes"][1]["inputs"]["text"] == "Échec : (échec de « Appel de création ») et {{ inputs.texte }}"
    )
    assert fixed["nodes"][0] == nodes[0]  # nothing else is touched
    assert svc._fix_failed_reads({"nodes": nodes, "edges": [{"from": "appel", "to": "suite"}]})[1] == 0


def test_the_reviewer_saying_the_same_thing_five_ways_is_one_remark():
    from ai.service import _merge_alike

    said = [
        {
            "step": None,
            "severity": "warning",
            "message": "Exigence manquante : Deux tentatives de redémarrage avec un délai de 30 secondes entre chacune",
        },
        {
            "step": "a",
            "severity": "warning",
            "message": "Le script ne contient pas la logique pour deux tentatives de redémarrage avec un délai de 30 secondes",
        },
        {
            "step": None,
            "severity": "error",
            "message": "Le workflow ne met pas en œuvre deux tentatives de redémarrage avec un délai de 30 secondes entre chacune",
        },
        {"step": "b", "severity": "warning", "message": "Aucune alerte n'est envoyée à l'équipe d'infrastructure"},
    ]
    kept = _merge_alike(said)
    assert len(kept) == 2 and kept[0]["severity"] == "error" and "infrastructure" in kept[1]["message"]


# ------------------------------------------------------------------------------------------------- several correction passes
UNMET = {
    "coverage": [{"requirement": "attendre 30 secondes puis réessayer", "status": "missing", "step": None}],
    "findings": [],
}
PART = {
    "coverage": [{"requirement": "attendre 30 secondes puis réessayer", "status": "partial", "step": "attente"}],
    "findings": [],
}
MET = {
    "coverage": [{"requirement": "attendre 30 secondes puis réessayer", "status": "done", "step": "attente"}],
    "findings": [],
}
PLAN_RETRY = {"steps": [{"id": "a", "type": "wait", "seconds": 1}]}
ADD_WAIT = {
    "reply": "ok",
    "ops": [{"op": "add_step", "step": {"type": "wait", "id": "attente", "seconds": 30}, "after": "a"}],
}
ADD_SECOND = {
    "reply": "ok",
    "ops": [{"op": "add_step", "step": {"type": "wait", "id": "encore", "seconds": 5}, "after": "attente"}],
}


def test_the_assistant_corrects_in_several_passes_until_the_request_is_met(dry, world):
    ws, _ = dry
    # pass 1 adds the wait (the reviewer then finds the requirement only partly met), pass 2 completes it, nothing is left
    model = Scripted(
        draft_workflow=PLAN_RETRY,
        review_workflow=[UNMET, PART, MET],
        repair_workflow=[ADD_WAIT, ADD_SECOND],
        dryrun_samples={},
    )
    graph, issues, check, _ = draft(ws, world["designer"], model)
    assert model.tasks.count("repair_workflow") == 2 and check["rounds"] == 2
    assert {n["id"] for n in graph["nodes"]} >= {"a", "attente", "encore"}
    assert "en 2 passe(s)" in service.check_sentence(check) and check["coverage"][0]["status"] == "done"


def test_a_pass_that_does_not_improve_stops_the_loop_and_is_not_kept(dry, world):
    ws, _ = dry
    useless = {"reply": "rien", "ops": [{"op": "add_note", "text": "sans effet"}]}
    model = Scripted(draft_workflow=PLAN_RETRY, review_workflow=UNMET, repair_workflow=useless, dryrun_samples={})
    graph, issues, check, _ = draft(ws, world["designer"], model)
    assert model.tasks.count("repair_workflow") == 1  # it tried once, saw no progress, and stopped (no endless loop)
    assert not graph.get("notes")  # the useless attempt was not kept
    assert any("Exigence manquante" in i["message"] for i in issues)  # what remains is said


def test_a_pass_that_makes_things_worse_is_thrown_away(dry, world):
    ws, _ = dry
    worse = {
        "reply": "casse",
        "ops": [
            {
                "op": "add_step",
                "step": {
                    "type": "script",
                    "id": "x",
                    "language": "python",
                    "script": "n = zzz",
                    "outputs": [{"name": "n", "type": "integer"}],
                },
                "after": "a",
            }
        ],
    }
    model = Scripted(draft_workflow=PLAN_RETRY, review_workflow=UNMET, repair_workflow=worse, dryrun_samples={})
    graph, *_ = draft(ws, world["designer"], model)
    assert {n["id"] for n in graph["nodes"]} == {"a"}  # the broken version was not kept


def test_the_number_of_passes_is_bounded(dry, world):
    ws, _ = dry
    many = [
        {
            "reply": "ok",
            "ops": [{"op": "add_step", "step": {"type": "wait", "id": f"w{i}", "seconds": i + 1}, "after": "a"}],
        }
        for i in range(6)
    ]
    model = Scripted(draft_workflow=PLAN_RETRY, review_workflow=[UNMET] * 8, repair_workflow=many, dryrun_samples={})
    draft(ws, world["designer"], model)
    assert model.tasks.count("repair_workflow") <= service.MAX_PASSES


def test_http_and_transform_steps_are_cleaned_forced_to_test_mode_and_unknown_params_dropped(ai_ws, world):
    from vault.models import Connection

    ws, _ = ai_ws
    Connection.objects.create(
        workspace=ws, name="slack", label="Slack", kind="http_bearer", config={"base_url": "https://slack.com/api"}
    )
    plan = {
        "steps": [
            {"id": "stats", "type": "action", "name": "Stats", "action": "text-stats", "inputs": {"text": "x"}},
            {
                "id": "liste",
                "type": "http",
                "name": "Canaux",
                "connection": "slack",
                "integration": "slack",
                "operation": "list_channels",
                "params": {"limit": 10, "invente": "x"},
                "mode": "reel",
            },
            {
                "id": "gros",
                "type": "transform",
                "op": "filter",
                "items": "{{ steps.liste.output.body }}",
                "where": "item.num_members > 3",
                "bogus": 1,
            },
            {
                "id": "libre",
                "type": "http",
                "connection": "slack",
                "method": "post",
                "path": "/chat.postMessage",
                "body": {"channel": "#a", "text": "hi"},
            },
        ]
    }
    model = Scripted(draft_workflow=plan, review_workflow=COVERED)
    graph, issues, *_ = draft(ws, world["designer"], model)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["liste"]["mode"] == "test" and nodes["liste"]["params"] == {"limit": 10}
    assert nodes["liste"]["integration"] == "slack" and "path" not in nodes["liste"]
    assert nodes["gros"]["items"] == "steps.liste.output.body" and "bogus" not in nodes["gros"]
    assert nodes["libre"]["method"] == "POST" and nodes["libre"]["mode"] == "test"
    assert [i for i in issues if i["level"] == "error"] == []
    assert any(i["code"] == "http_test_mode" for i in issues)  # the person is told nothing is sent yet


def test_the_model_sees_connections_and_integrations(ai_ws, world):
    ws, _ = ai_ws
    ctx = service._integrations_context()
    slack = next(i for i in ctx if i["slug"] == "slack")
    assert "channel*" in next(o for o in slack["operations"] if o["id"] == "post_message")["params"]
    assert service._connections_context(ws) == []
