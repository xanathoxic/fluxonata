"""Étape SSH : ce que voient les assistants externes (expurgé) et ce que la publication contrôle (sans évaluer)."""

import copy

from runs import ssh_node

MASKED = ssh_node.MASKED


def test_sensitive_commands_are_masked_and_their_simulations_dropped():
    node = {
        "type": "ssh",
        "commands": [
            {"id": "a", "run": "echo public"},
            {"id": "b", "run": "mysql -p'secret-12'", "sensitive": True},
            "texte",
        ],
        "mocks": [{"id": "a", "stdout": "ok"}, {"id": "b", "stdout": "secret-12"}],
    }
    before = copy.deepcopy(node)
    out = ssh_node.redact_node(node)
    assert (
        out["commands"][0]["run"] == "echo public"
        and out["commands"][1]["run"] == MASKED
        and out["commands"][2] == "texte"
    )
    assert out["mocks"] == [{"id": "a", "stdout": "ok"}]
    assert node == before  # the original step is never altered


def test_sensitive_session_steps_and_their_answers_are_masked():
    node = {
        "type": "ssh",
        "steps": [
            {"id": "s1", "send": "mot-de-passe", "send_secret": "alias", "responses": [{"expect": "x", "send": "y"}]},
            {
                "id": "s2",
                "send": "ls",
                "responses": [{"expect": "mdp", "send": "pw-12", "sensitive": True}, {"expect": "ok", "send": "oui"}],
            },
            {"expect": "invite"},
            "texte",
        ],
        "mocks": [{"id": "s1", "stdout": "x"}, {"id": "s2", "stdout": "y"}, {"id": "s9"}, "pas un objet"],
    }
    out = ssh_node.redact_node(node)
    s1, s2, s3, s4 = out["steps"]
    assert (
        s1["send"] == MASKED and s1["responses"][0]["send"] == MASKED
    )  # a step with a secret hides everything it sends
    assert s2["send"] == "ls" and s2["responses"][0]["send"] == MASKED and s2["responses"][1]["send"] == "oui"
    assert s3 == {"expect": "invite"} and s4 == "texte"
    assert out["mocks"] == [
        {"id": "s2", "stdout": "y"},
        {"id": "s9"},
        "pas un objet",
    ]  # s1 is hidden: its simulation goes too


def test_the_graph_is_redacted_step_by_step():
    assert ssh_node.redact_graph("pas un graphe") == "pas un graphe"
    assert ssh_node.redact_graph({"nodes": "x"}) == {"nodes": "x"}
    graph = {
        "nodes": [{"id": "n", "type": "ssh", "commands": [{"id": "a", "run": "x", "sensitive": True}]}, "autre"],
        "edges": [],
    }
    out = ssh_node.redact_graph(graph)
    assert out["nodes"][0]["commands"][0]["run"] == MASKED and out["nodes"][1] == "autre" and out["edges"] == []
    assert graph["nodes"][0]["commands"][0]["run"] == "x"


def test_publication_checks_without_evaluating_expressions():
    assert ssh_node.static_problems({"session": "exec", "commands": ["uptime"]}) == []
    assert (
        ssh_node.static_problems({"commands": "{{ inputs.cmds }}"}) == []
    )  # an expression is checked when the step starts
    assert ssh_node.static_problems({"commands": []}) == ["Indiquez au moins une commande."]
    assert "liste" in ssh_node.static_problems({"commands": 5})[0]
    assert ssh_node.static_problems({"session": "shell", "steps": "texte"}) == [
        "Indiquez au moins une étape de session."
    ]
    assert (
        ssh_node.static_problems(
            {"session": "shell", "steps": [{"id": "s1", "send": "ls", "expect": "{{ inputs.p }}"}]}
        )
        == []
    )
    assert "inconnue" in ssh_node.static_problems({"commands": ["ls"], "mocks": [{"id": "zzz"}]})[0]
    assert ssh_node.static_problems({"timeout_seconds": 0}) != []


def test_only_ssh_steps_are_redacted():
    other = {"type": "http", "commands": [{"run": "x", "sensitive": True}]}
    assert ssh_node.redact_node(other) is other


def test_a_chain_without_a_named_target_is_a_missing_connection():
    problems = ssh_node.chain_problems({"connection": ""}, None)
    assert problems and problems[0][0] == "missing_connection"
