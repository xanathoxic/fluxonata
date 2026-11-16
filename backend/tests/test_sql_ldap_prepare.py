"""Validation à la préparation des étapes SQL et LDAP (aucune base, aucun annuaire : connexion simulée)."""

import pytest

from runs import ldap_node, sql_node


def _res(v, ctx):
    return v


@pytest.fixture(autouse=True)
def _connections(monkeypatch):
    monkeypatch.setattr(sql_node, "find_connection", lambda *a: object())
    monkeypatch.setattr(ldap_node, "find_connection", lambda *a: object())


def _sql(**kw):
    return sql_node.prepare({"connection": "db", "statement": "select 1", **kw}, _res, {}, 1, None)


def test_sql_nominal_and_defaults():
    spec = _sql(params={"a": 1, "l": [1, 2]}, max_rows=5, timeout_seconds=30, mode="reel")
    assert spec["query"] == "select 1" and spec["max_rows"] == 5 and spec["timeout"] == 30 and spec["mode"] == "reel"
    spec = _sql(max_rows=10**9, timeout_seconds=999)
    assert spec["max_rows"] == 100 and spec["timeout"] == 15 and spec["mode"] == "test"


@pytest.mark.parametrize(
    "kw",
    [
        {"statement": ""},
        {"statement": "x" * (sql_node.MAX_QUERY + 1)},
        {"statement": "select 1; drop table t"},
        {"params": [1]},
        {"params": {f"p{i}": i for i in range(51)}},
        {"params": {"bad name": 1}},
        {"params": {"a": {"x": 1}}},
    ],
)
def test_sql_rejects(kw):
    with pytest.raises(sql_node.SqlStepError) as exc:
        _sql(**kw)
    assert exc.value.code == "SQL_CONFIG"


def test_sql_trailing_semicolon_is_dropped():
    assert sql_node.clean_query(" select 1 ; ") == "select 1"


def test_sql_missing_connection(monkeypatch):
    monkeypatch.setattr(sql_node, "find_connection", lambda *a: None)
    with pytest.raises(sql_node.SqlStepError) as exc:
        _sql()
    assert exc.value.code == "CONNECTION_MISSING" and exc.value.next_step


def test_sql_simulated_output_is_a_copy():
    out = sql_node.simulated_output({})
    out["x"] = 1
    assert "x" not in sql_node.simulated_output({})


def test_ldap_filter_escapes_values_and_checks_shape():
    assert ldap_node.build_filter("(mail={mail})", {"mail": "a*)(b"}) == r"(mail=a\2a\29\28b)"
    for bad in ("mail=x", "((mail=x)", "(" + "a" * 1000 + ")", ""):
        with pytest.raises(ldap_node.LdapStepError) as exc:
            ldap_node.build_filter(bad, {})
        assert exc.value.code == "LDAP_CONFIG"
    with pytest.raises(ldap_node.LdapStepError, match="manquant"):
        ldap_node.build_filter("(mail={mail})", {})


def test_ldap_prepare_rejects_bad_parameters(monkeypatch):
    base = {"connection": "ad", "filter": "(cn=x)", "base": "dc=x"}
    for params in ([1], {f"p{i}": 1 for i in range(21)}, {"a": [1]}, {"a": {"b": 1}}):
        with pytest.raises(ldap_node.LdapStepError) as exc:
            ldap_node.prepare({**base, "params": params}, _res, {}, 1, None)
        assert exc.value.code == "LDAP_CONFIG"
    monkeypatch.setattr(ldap_node, "find_connection", lambda *a: None)
    with pytest.raises(ldap_node.LdapStepError) as exc:
        ldap_node.prepare(base, _res, {}, 1, None)
    assert exc.value.code == "CONNECTION_MISSING"
