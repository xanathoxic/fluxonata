"""Caractérisation de `ai.speclint.spec_lint` : chaque famille de constats (variables recréées, noms demandés, textes cités, attentes,
bornes de boucle, comparaisons strictes ou larges, workflows cités) et l'ORDRE dans lequel ils sont rendus.

Écrit avant le découpage de la fonction : tout doit passer à l'identique avant et après."""

from ai.speclint import spec_lint


def lint(request, nodes=(), edges=(), ctx=None, **graph):
    g = {"inputs": {"properties": {}}, "nodes": list(nodes), "edges": list(edges), **graph}
    return spec_lint(request, g, ctx or {})


def messages(findings):
    return [f["message"] for f in findings]


def test_every_finding_is_an_error_shaped_like_the_reviewers():
    out = lint("Une attente de 2 minutes.")
    assert out == [
        {
            "step": None,
            "severity": "error",
            "message": "Une attente de 2 minute(s) est demandée : aucune étape d'attente ne dure 120 s.",
        }
    ]


def test_a_removal_request_in_additions_only_mode_checks_nothing():
    request = "supprime l'attente de 2 minutes"
    assert spec_lint(request, {"nodes": []}, {}, additions_only=True) == []
    assert spec_lint(request, {"nodes": []}, {}) != []  # without the option the same text is checked


def test_a_workflow_variable_must_not_be_recreated_as_a_script_output():
    n = {"id": "calc", "type": "script", "outputs": [{"name": "seuil"}, {"name": "autre"}, "pas un objet"]}
    out = lint("calcule", [n], variables=[{"name": "seuil"}, "ignoré"])
    assert len(out) == 1 and out[0]["step"] == "calc" and "« seuil » est déjà déclarée" in out[0]["message"]


def test_a_demanded_name_must_exist_somewhere_exactly():
    assert any("« moyenne » est demandé tel quel" in m for m in messages(lint("Renvoie la sortie moyenne, puis fin.")))
    present = [{"id": "s", "type": "script", "outputs": [{"name": "moyenne"}]}]
    assert lint("Renvoie la sortie moyenne, puis fin.", present) == []


def test_a_typed_name_is_recognised_and_sorted_alphabetically():
    out = messages(lint("Entrées : machines (liste de noms), seuil (nombre, défaut 3)"))
    assert [m.split("«")[1].split("»")[0].strip() for m in out] == ["machines", "seuil"]


def test_generic_words_and_names_of_the_graph_are_never_demanded():
    assert lint("la sortie texte, le champ liste") == []
    n = [{"id": "etape", "type": "script"}]
    assert lint("sortie etape,", n) == []
    inputs = {"inputs": {"properties": {"machines": {}}}}
    assert spec_lint("machines (liste de noms)", {"nodes": [], **inputs}, {}) == []


def test_a_name_is_found_in_a_skill_or_action_of_the_catalog():
    nodes = [{"id": "ia", "type": "ai", "skill": "resumer"}, {"id": "act", "type": "action", "action": "compter"}]
    ctx = {
        "skills": [{"slug": "resumer", "outputs": {"resume": {}}}],
        "catalog": [{"slug": "compter", "outputs": {"mots": {}}}],
    }
    assert lint("sortie resume, sortie mots,", nodes, ctx=ctx) == []


def test_a_quoted_label_must_appear_in_a_suitable_step():
    request = "Ajoute une exception avec le message « Montant refusé par le comptable »."
    assert any(
        "n'apparaît dans aucune étape adaptée" in m and "Il manque une étape exception" in m
        for m in messages(lint(request))
    )
    ok = [{"id": "t", "type": "throw", "message": "Montant refusé par le comptable"}]
    assert lint(request, ok) == []
    # an exception's text must be on an exception step, not just anywhere in the graph
    elsewhere = [
        {"id": "n", "type": "action", "description": "Montant refusé par le comptable"},
        {"id": "t", "type": "throw", "message": "autre"},
    ]
    assert lint(request, elsewhere) != []


def test_an_approval_title_must_be_on_an_approval_step():
    request = "Ajoute une approbation intitulée « Validation du budget annuel »"
    out = messages(lint(request))
    assert out and "Il manque une étape approbation" in out[0]
    assert lint(request, [{"id": "a", "type": "approval", "title": "Validation du budget annuel"}]) == []


def test_a_quoted_text_without_a_space_or_without_a_label_is_not_checked():
    assert lint("Appelle le message « montant_refuse » ici") == []
    assert lint("Voici un texte « Une phrase quelconque ici » sans étiquette connue") == []


def test_a_refusal_exception_must_hang_on_the_error_exit_of_the_step():
    request = "Si la validation échoue, ajoute une exception avec le message « Refusé par la direction générale »."
    nodes = [
        {"id": "v", "type": "action", "action": "valider"},
        {"id": "t", "type": "throw", "message": "Refusé par la direction générale"},
    ]
    ctx = {"catalog": []}
    assert spec_lint(request, {"nodes": nodes, "edges": [{"from": "v", "to": "t", "label": "next"}]}, ctx) != []
    assert spec_lint(request, {"nodes": nodes, "edges": [{"from": "v", "to": "t", "label": "error"}]}, ctx) == []


def test_a_requested_wait_must_exist_with_that_duration():
    assert lint("une pause de 2 heures", [{"id": "w", "type": "wait", "seconds": 7200}]) == []
    out = messages(lint("une pause de 2 heures", [{"id": "w", "type": "wait", "seconds": 60}]))
    assert out == ["Une attente de 2 heure(s) est demandée : aucune étape d'attente ne dure 7200 s."]


def test_loop_bounds_are_only_checked_when_the_graph_has_a_loop():
    request = "au plus 5 itérations, 3 en parallèle"
    assert lint(request) == []
    loop = [{"id": "b", "type": "foreach", "max_iterations": 9, "concurrency": 2}]
    out = messages(lint(request, loop))
    assert out == [
        "5 itérations au maximum sont demandées : max_iterations de la boucle doit valoir 5.",
        "3 en parallèle est demandé : concurrency de la boucle doit valoir 3.",
    ]
    assert lint(request, [{"id": "b", "type": "foreach", "max_iterations": 5, "concurrency": 3}]) == []


def test_a_strict_comparison_asks_for_the_strict_operator_only_when_the_wide_one_was_written():
    def cond(e):
        return [{"id": "c", "type": "condition", "expr": e}]

    request = "si la valeur est strictement supérieure à 100"
    assert messages(lint(request, cond("inputs.v >= 100"))) == [
        "« strictement supérieur à 100 » demande l'opérateur > et non >=."
    ]
    assert lint(request, cond("inputs.v > 100")) == []
    assert lint(request, cond("inputs.v == 100")) == []  # neither operator written: not the case this rule decides
    assert lint(request) == []  # no condition at all


def test_an_inclusive_comparison_asks_for_the_wide_operator():
    cond = [{"id": "c", "type": "condition", "expr": "inputs.v > 100"}]
    request = "si la valeur est supérieure ou égale à 100"
    assert messages(lint(request, cond)) == ["« supérieur ou égal à 100 » demande l'opérateur >=."]
    assert lint(request, [{"id": "c", "type": "condition", "expr": "inputs.v >= 100"}]) == []
    switch = [{"id": "s", "type": "switch", "cases": [{"id": "x", "expr": "inputs.v <= 5"}, "ignoré"]}]
    assert lint("inférieur ou égal à 5", switch) == []


def test_a_cited_workflow_must_be_called_by_a_step():
    ctx = {"workflows": [{"slug": "sauvegarde-base"}, {"slug": "autre"}]}
    out = messages(lint("lance sauvegarde-base ensuite", ctx=ctx))
    assert out == ["Le workflow « sauvegarde-base » est cité dans la demande mais aucune étape ne l'appelle."]
    assert (
        lint("lance sauvegarde-base", [{"id": "n", "type": "subworkflow", "workflow": "sauvegarde-base"}], ctx=ctx)
        == []
    )
    assert lint("lance sauvegarde-base-2", ctx=ctx) == []  # a longer name is not the same name


def test_findings_come_in_a_fixed_order_by_family():
    request = "Variables : seuil. sortie moyenne, une pause de 2 minutes, 4 itérations, 2 en parallèle, supérieur ou égal à 7, lance wf-x, message « Un message assez long »."
    nodes = [
        {"id": "calc", "type": "script", "outputs": [{"name": "seuil"}]},
        {"id": "b", "type": "foreach", "max_iterations": 9, "concurrency": 1},
        {"id": "c", "type": "condition", "expr": "inputs.v > 7"},
    ]
    out = messages(lint(request, nodes, variables=[{"name": "seuil"}], ctx={"workflows": [{"slug": "wf-x"}]}))
    families = [
        "La variable du workflow",
        "Le nom « moyenne »",
        "Le texte demandé",
        "Une attente de 2",
        "4 itérations",
        "2 en parallèle",
        "« supérieur ou égal",
        "Le workflow « wf-x",
    ]
    positions = [next(i for i, m in enumerate(out) if m.startswith(key)) for key in families]
    assert positions == list(range(len(families)))
