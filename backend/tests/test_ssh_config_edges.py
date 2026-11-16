"""Étape SSH : validation des alias de secrets et des simulations (aucune connexion)."""

import pytest

from runs.ssh_core import SshStepError, clean_mocks, clean_secret_aliases


def refused(fn, raw):
    with pytest.raises(SshStepError) as exc:
        fn(raw)
    assert exc.value.code == "SSH_CONFIG"
    return exc.value.message


def test_secret_aliases_are_simple_names_pointing_at_fixed_secret_names():
    assert clean_secret_aliases(None) == {} and clean_secret_aliases({}) == {}
    assert clean_secret_aliases({"db_pass": "secret-db"}) == {"db_pass": "secret-db"}
    refused(clean_secret_aliases, ["pas", "un", "objet"])
    refused(clean_secret_aliases, {f"a{i}": "s" for i in range(11)})
    for bad in ({"mauvais alias": "s"}, {"a": ""}, {"a": 5}, {"a": "{{ inputs.s }}"}, {"x" * 41: "s"}):
        assert "alias simple" in refused(clean_secret_aliases, bad)


def test_simulations_are_a_bounded_list_of_entries_with_an_id():
    assert clean_mocks(None) == {} and clean_mocks([]) == {} and clean_mocks({}) == {}
    assert clean_mocks([{"id": "c1", "stdout": "ok", "exit_status": 0, "extra": 1}]) == {
        "c1": {"stdout": "ok", "exit_status": 0}
    }
    assert clean_mocks({"c2": {"stderr": "non", "exit_status": 2}, "ignoré": "pas un objet"}) == {
        "c2": {"stderr": "non", "exit_status": 2}
    }
    assert "100 au plus" in refused(clean_mocks, [{"id": f"c{i}"} for i in range(101)])
    assert "100 au plus" in refused(clean_mocks, "texte")
    for bad in (["pas un objet"], [{"stdout": "x"}], [{"id": 3}]):
        assert "« id »" in refused(clean_mocks, bad)


def test_simulated_output_and_status_are_bounded():
    assert "512 Ko" in refused(clean_mocks, [{"id": "c", "stdout": "x" * 524289}])
    assert "512 Ko" in refused(clean_mocks, [{"id": "c", "stderr": 5}])
    for status in (-1, 256, True, "0", 1.5):
        assert "0 à 255" in refused(clean_mocks, [{"id": "c", "exit_status": status}])
    assert clean_mocks([{"id": "c", "exit_status": 255}]) == {"c": {"exit_status": 255}}
